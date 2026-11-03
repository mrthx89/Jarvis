plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.google.services)
}

// Secrets are read from the local environment, never committed.
val releaseStore = providers.environmentVariable("NEXO_KEYSTORE_PATH").orNull
    ?: providers.environmentVariable("JARVIS_KEYSTORE_PATH").orNull
val releaseStorePassword = providers.environmentVariable("NEXO_KEYSTORE_PASSWORD").orNull
    ?: providers.environmentVariable("JARVIS_KEYSTORE_PASSWORD").orNull
val releaseAlias = providers.environmentVariable("NEXO_KEY_ALIAS").orNull
    ?: providers.environmentVariable("JARVIS_KEY_ALIAS").orNull
val releaseKeyPassword = providers.environmentVariable("NEXO_KEY_PASSWORD").orNull
    ?: providers.environmentVariable("JARVIS_KEY_PASSWORD").orNull
val signingValues = listOf(releaseStore, releaseStorePassword, releaseAlias, releaseKeyPassword)
require(signingValues.all { it == null } || signingValues.all { !it.isNullOrBlank() }) {
    "Set all four NEXO_KEYSTORE_* / JARVIS_KEYSTORE_* signing variables, or none for unsigned release."
}

android {
    namespace = "id.my.yhdev.nexo"
    compileSdk = 36

    defaultConfig {
        applicationId = "id.my.yhdev.nexo"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    testOptions { unitTests.isReturnDefaultValues = true }

    lint {
        checkReleaseBuilds = true
        abortOnError = true
    }

    signingConfigs {
        if (signingValues.all { !it.isNullOrBlank() }) {
            create("production") {
                storeFile = file(requireNotNull(releaseStore))
                storePassword = releaseStorePassword
                keyAlias = releaseAlias
                keyPassword = releaseKeyPassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("production")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
        debug {
            isMinifyEnabled = false
        }
        create("internal") {
            initWith(getByName("release"))
            signingConfig = signingConfigs.getByName("debug")
            matchingFallbacks += listOf("release")
            versionNameSuffix = "-internal"
            // Same applicationId deliberately retains the bundled Firebase client match.
            // It cannot coexist with a differently signed production install.
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        viewBinding = true
    }
}

dependencies {
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.20.0")
    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.activity.ktx)
    implementation(libs.google.material)
    implementation(libs.androidx.constraintlayout)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.service)

    // Firebase BoM & Remote Config (for dynamic server URL switching)
    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.config.ktx)
    implementation(libs.firebase.analytics.ktx)
    implementation(libs.firebase.messaging.ktx)

    // Networking: OkHttp for low-latency WebSocket connection
    implementation(libs.okhttp)

    // JSON parsing
    implementation(libs.gson)

    // Coroutines
    implementation(libs.kotlinx.coroutines.android)
}

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.gms.google-services")
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
    compileSdk = 34

    defaultConfig {
        applicationId = "id.my.yhdev.nexo"
        minSdk = 26
        targetSdk = 34
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
    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")
    implementation("androidx.lifecycle:lifecycle-service:2.7.0")

    // Firebase BoM & Remote Config (for dynamic server URL switching)
    implementation(platform("com.google.firebase:firebase-bom:33.7.0"))
    implementation("com.google.firebase:firebase-config-ktx")
    implementation("com.google.firebase:firebase-analytics-ktx")
    implementation("com.google.firebase:firebase-messaging-ktx")

    // Networking: OkHttp for low-latency WebSocket connection
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // JSON parsing
    implementation("com.google.code.gson:gson:2.10.1")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.0")
}

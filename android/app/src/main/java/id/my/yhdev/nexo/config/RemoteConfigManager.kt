package id.my.yhdev.nexo.config

import android.content.Context
import android.util.Log
import com.google.firebase.FirebaseApp
import com.google.firebase.remoteconfig.ConfigUpdate
import com.google.firebase.remoteconfig.ConfigUpdateListener
import com.google.firebase.remoteconfig.ConfigUpdateListenerRegistration
import com.google.firebase.remoteconfig.FirebaseRemoteConfig
import com.google.firebase.remoteconfig.FirebaseRemoteConfigException
import com.google.firebase.remoteconfig.ktx.remoteConfigSettings
import id.my.yhdev.nexo.R

class RemoteConfigManager(private val context: Context) {

    private val TAG = "RemoteConfigManager"
    val KEY_SERVER_URL = "server_websocket_url"

    private var remoteConfig: FirebaseRemoteConfig? = null
    private var registration: ConfigUpdateListenerRegistration? = null
    private var closed = false
    private val preferences = context.getSharedPreferences("nexo_endpoint", Context.MODE_PRIVATE)
    private val legacyPreferences = context.getSharedPreferences("nexo_endpoint", Context.MODE_PRIVATE)

    private fun safeFallback(): String? {
        val lastValid = preferences.getString("last_valid_url", null)
            ?: legacyPreferences.getString("last_valid_url", null)
        return ServerEndpointPolicy.fallback(
            lastValid,
            context.getString(R.string.default_server_url)
        )
    }

    private fun validated(value: String): String? {
        if (ServerEndpointPolicy.isValid(value)) {
            // Last syntactically validated endpoint, not a claim of backend readiness.
            preferences.edit().putString("last_valid_url", value).apply()
            return value
        }
        Log.w(TAG, "Ignored invalid Remote Config endpoint; retaining validated endpoint")
        return null
    }

    init {
        try {
            if (FirebaseApp.getApps(context).isNotEmpty()) {
                remoteConfig = FirebaseRemoteConfig.getInstance().apply {
                    val configSettings = remoteConfigSettings {
                        // Realtime updates do not require disabling normal fetch caching.
                        minimumFetchIntervalInSeconds = 3600
                    }
                    setConfigSettingsAsync(configSettings)

                    // Default fallback values
                    val defaultUrl = context.getString(R.string.default_server_url)
                    setDefaultsAsync(mapOf(KEY_SERVER_URL to defaultUrl))
                }
            } else {
                Log.w(TAG, "FirebaseApp is not initialized. Using local fallback.")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Firebase Remote Config initialization failed: ${e.message}. Using local fallback.")
        }
    }

    /**
     * Resolves the server WebSocket URL.
     * Fetches from Firebase Remote Config asynchronously and falls back to local default.
     */
    fun fetchServerUrl(onUnavailable: () -> Unit, onResolved: (String) -> Unit) {
        val defaultUrl = safeFallback()
        val config = remoteConfig

        if (config == null) {
            if (defaultUrl != null) onResolved(defaultUrl) else onUnavailable()
            return
        }

        // Return current active cached value first for instantaneous startup
        val cachedUrl = validated(config.getString(KEY_SERVER_URL)) ?: defaultUrl
        if (cachedUrl != null) onResolved(cachedUrl) else onUnavailable()

        // Asynchronously fetch latest values from Firebase
        config.fetchAndActivate()
            .addOnCompleteListener { task ->
                if (closed) return@addOnCompleteListener
                if (task.isSuccessful) {
                    val latestUrl = validated(config.getString(KEY_SERVER_URL))
                    if (latestUrl != null && latestUrl != cachedUrl) {
                        Log.i(TAG, "Validated server endpoint updated")
                        onResolved(latestUrl)
                    }
                } else {
                    Log.w(TAG, "Remote Config fetch failed: ${task.exception?.message}")
                }
            }
    }

    /**
     * Listens for real-time config updates from Firebase Console.
     */
    fun listenForRealtimeUpdates(onUrlChanged: (String) -> Unit) {
        val config = remoteConfig ?: return

        registration?.remove()
        registration = config.addOnConfigUpdateListener(object : ConfigUpdateListener {
            override fun onUpdate(configUpdate: ConfigUpdate) {
                if (configUpdate.updatedKeys.contains(KEY_SERVER_URL)) {
                    config.activate().addOnCompleteListener {
                        if (closed) return@addOnCompleteListener
                        if (!it.isSuccessful) return@addOnCompleteListener
                        val newUrl = validated(config.getString(KEY_SERVER_URL))
                        if (newUrl != null) {
                            Log.i(TAG, "Validated realtime server endpoint updated")
                            onUrlChanged(newUrl)
                        }
                    }
                }
            }

            override fun onError(error: FirebaseRemoteConfigException) {
                Log.w(TAG, "ConfigUpdateListener error: ${error.message}")
            }
        })
    }

    fun close() {
        closed = true
        registration?.remove()
        registration = null
        remoteConfig = null
    }
}

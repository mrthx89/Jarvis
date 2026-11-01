package id.my.yhdev.jarvis.config

import android.content.Context
import android.util.Log
import com.google.firebase.FirebaseApp
import com.google.firebase.remoteconfig.ConfigUpdate
import com.google.firebase.remoteconfig.ConfigUpdateListener
import com.google.firebase.remoteconfig.ConfigUpdateListenerRegistration
import com.google.firebase.remoteconfig.FirebaseRemoteConfig
import com.google.firebase.remoteconfig.FirebaseRemoteConfigException
import com.google.firebase.remoteconfig.ktx.remoteConfigSettings
import id.my.yhdev.jarvis.R

class RemoteConfigManager(private val context: Context) {

    private val TAG = "RemoteConfigManager"
    val KEY_SERVER_URL = "server_websocket_url"

    private var remoteConfig: FirebaseRemoteConfig? = null
    private var registration: ConfigUpdateListenerRegistration? = null
    private var closed = false

    init {
        try {
            if (FirebaseApp.getApps(context).isNotEmpty()) {
                remoteConfig = FirebaseRemoteConfig.getInstance().apply {
                    val configSettings = remoteConfigSettings {
                        // 0 seconds for instant updates in appliance deployment
                        minimumFetchIntervalInSeconds = 0
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
    fun fetchServerUrl(onResolved: (String) -> Unit) {
        val defaultUrl = context.getString(R.string.default_server_url)
        val config = remoteConfig

        if (config == null) {
            onResolved(defaultUrl)
            return
        }

        // Return current active cached value first for instantaneous startup
        val cachedUrl = config.getString(KEY_SERVER_URL).takeIf { it.isNotBlank() } ?: defaultUrl
        onResolved(cachedUrl)

        // Asynchronously fetch latest values from Firebase
        config.fetchAndActivate()
            .addOnCompleteListener { task ->
                if (closed) return@addOnCompleteListener
                if (task.isSuccessful) {
                    val latestUrl = config.getString(KEY_SERVER_URL)
                    if (latestUrl.isNotBlank() && latestUrl != cachedUrl) {
                        Log.i(TAG, "New server URL fetched from Remote Config: $latestUrl")
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
                        val newUrl = config.getString(KEY_SERVER_URL)
                        if (newUrl.isNotBlank()) {
                            Log.i(TAG, "Realtime Remote Config update received: $newUrl")
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
    }
}

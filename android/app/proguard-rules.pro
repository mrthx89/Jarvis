# Jarvis Appliance Proguard Rules
-keep class com.yhdev.jarvis.network.ProtocolModels$* { *; }
-keepclassmembers class com.yhdev.jarvis.network.ProtocolModels$* { *; }
-dontwarn okhttp3.**
-dontwarn okio.**

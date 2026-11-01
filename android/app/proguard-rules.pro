# Nexo Appliance Proguard Rules
-keep class id.my.yhdev.nexo.network.ProtocolModels$* { *; }
-keepclassmembers class id.my.yhdev.nexo.network.ProtocolModels$* { *; }
-keep class id.my.yhdev.jarvis.network.ProtocolModels$* { *; }
-keepclassmembers class id.my.yhdev.jarvis.network.ProtocolModels$* { *; }
-dontwarn okhttp3.**
-dontwarn okio.**

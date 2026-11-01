package id.my.yhdev.nexo.ui

enum class NexoState {
    IDLE,
    LISTENING,
    THINKING,
    SPEAKING,
    ERROR,
    DISCONNECTED
}

typealias JarvisState = NexoState

package id.my.yhdev.nexo.ui

import id.my.yhdev.nexo.R

enum class MascotExpression(val drawableRes: Int, val idName: String) {
    NETRAL(R.drawable.mascot_netral, "netral"),
    SENANG(R.drawable.mascot_senang, "senang"),
    SANGAT_SENANG(R.drawable.mascot_sangat_senang, "sangat_senang"),
    TERTAWA(R.drawable.mascot_tertawa, "tertawa"),
    BINGUNG(R.drawable.mascot_bingung, "bingung"),
    MENGANTUK(R.drawable.mascot_mengantuk, "mengantuk"),
    MENGEDIP(R.drawable.mascot_mengedip, "mengedip"),
    TERKEJUT(R.drawable.mascot_terkejut, "terkejut"),
    CINTA(R.drawable.mascot_cinta, "cinta"),
    JAHIL(R.drawable.mascot_jahil, "jahil"),
    KEREN(R.drawable.mascot_keren, "keren"),
    MALU(R.drawable.mascot_malu, "malu"),
    MARAH(R.drawable.mascot_marah, "marah"),
    MENANGIS(R.drawable.mascot_menangis, "menangis"),
    SEDIH(R.drawable.mascot_sedih, "sedih"),
    TAKUT(R.drawable.mascot_takut, "takut"),
    SLEEPING(R.drawable.mascot_sleeping, "sleeping");

    companion object {
        fun fromString(name: String?): MascotExpression {
            if (name == null) return NETRAL
            val normalized = name.lowercase().replace("-", "_")
            if (normalized == "tidur") return SLEEPING
            if (normalized == "bangun") return NETRAL
            return entries.find { it.idName == normalized } ?: NETRAL
        }
    }
}

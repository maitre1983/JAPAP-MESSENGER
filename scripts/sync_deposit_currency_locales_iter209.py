"""iter209 — sync `deposit_currency` keys to all 11 locales with translations
keyed by language. Uses pre-translated strings (short fixed UI labels) for
deterministic output without LLM round-trips.
"""
import json
from pathlib import Path

LOCALES = Path("/app/frontend/src/locales")

DEPOSIT_CURRENCY = {
    "fr": {
        "local_currency":            "Devise locale",
        "local_equivalent":          "Équivalent local",
        "rate_loading":              "Calcul du taux…",
        "rate_error":                "Taux indisponible",
        "enter_amount_to_preview":   "Saisissez un montant pour voir la conversion",
        "select_currency":           "Sélectionner une devise",
    },
    "en": {
        "local_currency":            "Local currency",
        "local_equivalent":          "Local equivalent",
        "rate_loading":              "Loading rate…",
        "rate_error":                "Rate unavailable",
        "enter_amount_to_preview":   "Enter an amount to preview the conversion",
        "select_currency":           "Select a currency",
    },
    "pt": {
        "local_currency":            "Moeda local",
        "local_equivalent":          "Equivalente local",
        "rate_loading":              "A calcular taxa…",
        "rate_error":                "Taxa indisponível",
        "enter_amount_to_preview":   "Insira um valor para ver a conversão",
        "select_currency":           "Selecionar uma moeda",
    },
    "es": {
        "local_currency":            "Moneda local",
        "local_equivalent":          "Equivalente local",
        "rate_loading":              "Calculando tasa…",
        "rate_error":                "Tasa no disponible",
        "enter_amount_to_preview":   "Ingrese un monto para ver la conversión",
        "select_currency":           "Seleccionar una moneda",
    },
    "ar": {
        "local_currency":            "العملة المحلية",
        "local_equivalent":          "ما يعادل بالعملة المحلية",
        "rate_loading":              "جارٍ حساب السعر…",
        "rate_error":                "السعر غير متاح",
        "enter_amount_to_preview":   "أدخل مبلغًا لمعاينة التحويل",
        "select_currency":           "اختر عملة",
    },
    "sw": {
        "local_currency":            "Sarafu ya ndani",
        "local_equivalent":          "Sawa na sarafu ya ndani",
        "rate_loading":              "Inakokotoa kiwango…",
        "rate_error":                "Kiwango hakipatikani",
        "enter_amount_to_preview":   "Ingiza kiasi ili kuona ubadilishaji",
        "select_currency":           "Chagua sarafu",
    },
    "ln": {
        "local_currency":            "Mbongo ya mboka",
        "local_equivalent":          "Bo ndenge moko na mbongo ya mboka",
        "rate_loading":              "Kotanga ntɛn…",
        "rate_error":                "Ntɛn ezali te",
        "enter_amount_to_preview":   "Tya motuya mpo ya kotala mbongwana",
        "select_currency":           "Pona mbongo",
    },
    "yo": {
        "local_currency":            "Owó agbègbè",
        "local_equivalent":          "Ìdọ́gba ní owó agbègbè",
        "rate_loading":              "Ń ka iye…",
        "rate_error":                "Iye kò sí",
        "enter_amount_to_preview":   "Tẹ owó kan láti rí ìpadàrí",
        "select_currency":           "Yan owó kan",
    },
    "hi": {
        "local_currency":            "स्थानीय मुद्रा",
        "local_equivalent":          "स्थानीय बराबर",
        "rate_loading":              "दर की गणना हो रही है…",
        "rate_error":                "दर उपलब्ध नहीं",
        "enter_amount_to_preview":   "रूपांतरण देखने के लिए राशि दर्ज करें",
        "select_currency":           "मुद्रा चुनें",
    },
    "bn": {
        "local_currency":            "স্থানীয় মুদ্রা",
        "local_equivalent":          "স্থানীয় সমতুল্য",
        "rate_loading":              "হার গণনা করা হচ্ছে…",
        "rate_error":                "হার উপলব্ধ নয়",
        "enter_amount_to_preview":   "রূপান্তর দেখতে পরিমাণ লিখুন",
        "select_currency":           "একটি মুদ্রা নির্বাচন করুন",
    },
    "ta": {
        "local_currency":            "உள்ளூர் நாணயம்",
        "local_equivalent":          "உள்ளூர் சமமான மதிப்பு",
        "rate_loading":              "விகிதம் கணக்கிடப்படுகிறது…",
        "rate_error":                "விகிதம் கிடைக்கவில்லை",
        "enter_amount_to_preview":   "மாற்றத்தை முன்னோட்டமிட தொகையை உள்ளிடவும்",
        "select_currency":           "ஒரு நாணயத்தைத் தேர்ந்தெடுக்கவும்",
    },
}


def main():
    for lang_code, translations in DEPOSIT_CURRENCY.items():
        path = LOCALES / f"{lang_code}.json"
        if not path.exists():
            print(f"  ! missing {path}")
            continue
        with open(path) as f:
            data = json.load(f)
        wallet = data.setdefault("wallet", {})
        wallet["deposit_currency"] = translations
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  ✓ {lang_code}: deposit_currency.{{local_currency,local_equivalent,rate_loading,rate_error,enter_amount_to_preview,select_currency}}")
    print(f"\nDone — {len(DEPOSIT_CURRENCY)} locales updated.")


if __name__ == "__main__":
    main()

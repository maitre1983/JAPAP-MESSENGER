"""Retry script for the i18n bundles that failed on the first pass."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gen_i18n_bundles import (  # noqa: E402
    _api_key, _validate_same_shape, translate_bundle, FRONTEND_LOCALES, SOURCE, TARGETS
)
import json


async def main(codes):
    api_key = _api_key()
    src = json.loads(SOURCE.read_text())
    src_json = json.dumps(src, ensure_ascii=False, indent=2)
    for c in codes:
        if c not in TARGETS:
            print(f"skip unknown: {c}")
            continue
        name, note = TARGETS[c]
        # Retry up to 4 times with exponential back-off
        for attempt in range(4):
            try:
                print(f"[i18n] retry {c} ({name}) attempt {attempt + 1}…")
                bundle = await translate_bundle(api_key, c, name, note, src_json)
                errs = _validate_same_shape(src, bundle)
                if errs:
                    print(f"[i18n]   shape errors: {errs[:2]} — retrying")
                    continue
                out = FRONTEND_LOCALES / f"{c}.json"
                out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
                print(f"[i18n] ✅ {c} saved")
                break
            except Exception as e:
                wait = 2 ** attempt
                print(f"[i18n]   failed ({e}); sleeping {wait}s")
                await asyncio.sleep(wait)


if __name__ == "__main__":
    import sys
    codes = sys.argv[1:] or ["yo", "ta"]
    asyncio.run(main(codes))

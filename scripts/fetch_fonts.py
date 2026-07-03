"""Download the bundled UI fonts (all OFL-licensed) into vc_translator/webui/fonts/.

Sources are the official Google Fonts GitHub repositories (raw TTFs), which are
stable direct-download URLs. Run once; the fonts are committed to the repo.
"""
from pathlib import Path
from urllib.request import urlretrieve

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "vc_translator" / "webui" / "fonts"
OUT.mkdir(parents=True, exist_ok=True)

FONTS = {
    # variable fonts keep the bundle small while covering all needed weights
    "SpaceGrotesk.ttf":
        "https://raw.githubusercontent.com/google/fonts/main/ofl/spacegrotesk/SpaceGrotesk%5Bwght%5D.ttf",
    "JetBrainsMono.ttf":
        "https://raw.githubusercontent.com/google/fonts/main/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
    "NotoSansJP.ttf":
        "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansjp/NotoSansJP%5Bwght%5D.ttf",
}

for name, url in FONTS.items():
    dest = OUT / name
    if dest.exists() and dest.stat().st_size > 10000:
        print(f"skip (exists): {name}")
        continue
    print(f"downloading {name} ...")
    urlretrieve(url, dest)
    print(f"  -> {dest.stat().st_size / 1024:.0f} KB")

print("done")

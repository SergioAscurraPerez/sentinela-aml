"""Descarga los datasets de Kaggle con kagglehub y los copia a data/raw/.

Uso:
    python data/download_data.py            # SAML-D completo + IBM AML (solo HI-Small)
    python data/download_data.py --solo-saml

Notas:
- IBM AML completo pesa varios GB. Aquí se baja solo HI-Small_Trans.csv, que basta para el hack.
- Si kagglehub responde 401/403: ejecuta `python -c "import kagglehub; kagglehub.login()"`
  o deja tu kaggle.json en ~/.kaggle/ (Windows: C:\\Users\\<tu_usuario>\\.kaggle\\kaggle.json).
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import kagglehub

RAW = Path(__file__).resolve().parent / "raw"

SAML = "berkanoztas/synthetic-transaction-monitoring-dataset-aml"
IBM = "ealtman2019/ibm-transactions-for-anti-money-laundering-aml"
IBM_FILE = "HI-Small_Trans.csv"


def copy_csvs(src: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    files = [src] if src.is_file() else list(src.rglob("*.csv"))
    for f in files:
        target = dest / f.name
        if not target.exists():
            shutil.copy2(f, target)
        out.append(target)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo-saml", action="store_true")
    args = ap.parse_args()

    print("Descargando SAML-D ...")
    p = Path(kagglehub.dataset_download(SAML))
    for f in copy_csvs(p, RAW / "saml_d"):
        print("  ->", f, f"{f.stat().st_size/1e6:.0f} MB")

    if not args.solo_saml:
        print(f"Descargando IBM AML (solo {IBM_FILE}) ...")
        try:
            p = Path(kagglehub.dataset_download(IBM, path=IBM_FILE))
        except TypeError:
            # kagglehub antiguo sin parámetro path: baja todo (pesado)
            p = Path(kagglehub.dataset_download(IBM))
        for f in copy_csvs(p, RAW / "ibm"):
            if f.name == IBM_FILE or p.is_file():
                print("  ->", f, f"{f.stat().st_size/1e6:.0f} MB")

    print("Listo. Siguiente paso: python data/prepare_data.py --source samld")


if __name__ == "__main__":
    main()

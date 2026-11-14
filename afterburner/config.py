"""Strict, local configuration. No credentials from JAFAR are read."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
SYMBOL = re.compile(r"^[A-Z0-9]{2,32}-USDT$")


def load_env_file(path: Path | None = None) -> None:
    if path is None:
        secure_path = Path.home() / ".config" / "afterburner" / ".env"
        path = secure_path if secure_path.is_file() else ROOT / ".env"
    if not path.is_file():
        return
    # Parse only KEY=value, never execute shell from config.
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.startswith("AFTERBURNER_") or not key.isidentifier():
            continue
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def read_yaml(name: str) -> dict:
    with (ROOT / "config" / name).open() as stream:
        result = yaml.safe_load(stream)
    if not isinstance(result, dict):
        raise ValueError(f"Invalid YAML mapping: {name}")
    return result


@dataclass(frozen=True)
class Asset:
    symbol: str
    label: str
    ticker: str
    dex_chain: str
    candidate_mint: str
    mint_verified: bool
    verification_evidence: str
    news_aliases: tuple[str, ...]


def assets() -> list[Asset]:
    output = []
    seen = set()
    for raw in read_yaml("watchlist.yaml")["assets"]:
        symbol = str(raw["symbol"]).upper()
        if not SYMBOL.fullmatch(symbol) or symbol in seen:
            raise ValueError(f"Invalid or duplicate BingX symbol: {symbol}")
        seen.add(symbol)
        address = str(raw.get("candidate_mint", ""))
        if address and not SOLANA_ADDRESS.fullmatch(address):
            raise ValueError(f"Invalid Solana candidate address: {symbol}")
        verified = raw.get("mint_verified", False)
        if not isinstance(verified, bool):
            raise ValueError(f"mint_verified must be boolean for {symbol}")
        evidence = str(raw.get("verification_evidence") or "")
        if verified and (not address or len(evidence.strip()) < 12):
            raise ValueError(f"verified mint needs address and documented evidence: {symbol}")
        output.append(Asset(symbol, str(raw["label"]), str(raw["ticker"]).upper(),
                            str(raw.get("dex_chain", "solana")), address,
                            verified, evidence, tuple(raw.get("news_aliases", []))))
    return output


def settings() -> dict:
    return read_yaml("settings.yaml")


def sources() -> list[dict]:
    feeds = read_yaml("sources.yaml").get("feeds", [])
    if not isinstance(feeds, list):
        raise ValueError("sources must be a list")
    for feed in feeds:
        if not str(feed.get("url", "")).startswith("https://"):
            raise ValueError("source URL must be HTTPS")
    return feeds


def data_dir() -> Path:
    load_env_file()
    directory = Path(os.environ.get("AFTERBURNER_DATA_DIR") or
                     Path.home() / ".local" / "state" / "afterburner").expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    return directory

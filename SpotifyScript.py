#!/usr/bin/env python3
"""
Spotify Data Enrichment Script
Fetches album cover artwork and Spotify metadata for tracks in the dataset using the Spotify Web API.
"""

import argparse
import os
from pathlib import Path
import sys
import time
from typing import Optional

import pandas as pd
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, desc=""):
        print(f"Starting {desc}...")
        return iterable


def get_spotify_token(client_id: str, client_secret: str) -> Optional[str]:
    """
    Authenticates against Spotify Web API using Client Credentials flow.
    Returns access token string or None on failure.
    """
    auth_url = "https://accounts.spotify.com/api/token"
    try:
        response = requests.post(
            auth_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if response.status_code == 200:
            return response.json().get("access_token")
        else:
            print(f"❌ Authentication failed: HTTP {response.status_code} - {response.text}")
            return None
    except requests.RequestException as e:
        print(f"❌ Network error while requesting token: {e}")
        return None


def search_track(track_name: str, artist_name: str, token: str) -> Optional[str]:
    """
    Searches Spotify catalog for a track and returns its Spotify track ID.
    """
    clean_artist = artist_name.split(",")[0].strip() if artist_name else ""
    query = f"{track_name} artist:{clean_artist}" if clean_artist else track_name
    search_url = "https://api.spotify.com/v1/search"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"q": query, "type": "track", "limit": 1}

    try:
        response = requests.get(search_url, headers=headers, params=params, timeout=10)
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 2))
            time.sleep(retry_after)
            return search_track(track_name, artist_name, token)

        if response.status_code == 200:
            data = response.json()
            items = data.get("tracks", {}).get("items", [])
            if items:
                return items[0].get("id")
    except requests.RequestException:
        pass
    return None


def get_track_album_image(track_id: str, token: str) -> Optional[str]:
    """
    Retrieves the primary album cover image URL for a given Spotify track ID.
    """
    track_url = f"https://api.spotify.com/v1/tracks/{track_id}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.get(track_url, headers=headers, timeout=10)
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 2))
            time.sleep(retry_after)
            return get_track_album_image(track_id, token)

        if response.status_code == 200:
            data = response.json()
            images = data.get("album", {}).get("images", [])
            if images:
                return images[0].get("url")
    except requests.RequestException:
        pass
    return None


def find_input_file(custom_path: Optional[str] = None) -> Optional[Path]:
    """
    Locates the input dataset file dynamically across standard workspace directories.
    """
    if custom_path and Path(custom_path).exists():
        return Path(custom_path)

    env_path = os.getenv("SPOTIFY_CSV_PATH")
    if env_path and Path(env_path).exists():
        return Path(env_path)

    script_dir = Path(__file__).resolve().parent
    candidates = [
        script_dir / "spotify-2023.csv",
        script_dir / "data" / "spotify-2023.csv",
        script_dir.parent / "spotify-2023.csv",
        script_dir / "spotify-2023.xlsx",
    ]

    for cand in candidates:
        if cand.exists():
            return cand
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich Spotify dataset with Album Art URLs from Spotify Web API.")
    parser.add_argument("--input", "-i", type=str, help="Path to input Spotify dataset CSV/Excel")
    parser.add_argument("--output", "-o", type=str, default="spotify_enriched_data.csv", help="Output enriched CSV filename")
    parser.add_argument("--client-id", type=str, default=os.getenv("SPOTIFY_CLIENT_ID"), help="Spotify Developer Client ID")
    parser.add_argument("--client-secret", type=str, default=os.getenv("SPOTIFY_CLIENT_SECRET"), help="Spotify Developer Client Secret")
    args = parser.parse_args()

    client_id = args.client_id
    client_secret = args.client_secret

    if not client_id or not client_secret:
        print("⚠️ Spotify API credentials not found in environment or arguments.")
        print("   Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env or pass as arguments.")
        print("   Example: python SpotifyScript.py --client-id <ID> --client-secret <SECRET>\n")

    input_file = find_input_file(args.input)
    if not input_file:
        print(f"❌ Input dataset file not found. Place 'spotify-2023.csv' in the project directory or pass --input <path>.")
        sys.exit(1)

    print(f"📖 Loading input dataset from: {input_file}")
    try:
        if input_file.suffix.lower() in [".xlsx", ".xls"]:
            df = pd.read_excel(input_file)
        else:
            df = pd.read_csv(input_file, encoding="ISO-8859-1")
    except Exception as e:
        print(f"❌ Error loading file: {e}")
        sys.exit(1)

    print(f"📊 Dataset loaded successfully ({len(df)} rows).")

    if not client_id or not client_secret:
        print("⏭️ Skipping API enrichment due to missing credentials. Saving copy of dataset...")
        df.to_csv(args.output, index=False)
        print(f"✅ Saved dataset to {args.output}")
        return

    print("🔐 Authenticating with Spotify Web API...")
    token = get_spotify_token(client_id, client_secret)
    if not token:
        print("❌ Failed to obtain Spotify access token. Exiting.")
        sys.exit(1)
    print("✅ Successfully authenticated.")

    if "image_url" not in df.columns:
        df["image_url"] = None
    if "spotify_track_id" not in df.columns:
        df["spotify_track_id"] = None

    print(f"🚀 Enriching {len(df)} tracks with Spotify album art...")
    enriched_count = 0

    for idx, row in tqdm(df.iterrows(), desc="Fetching Track Artwork"):
        if pd.notna(row.get("image_url")) and str(row.get("image_url")).strip():
            continue

        track_name = str(row.get("track_name", "")).strip()
        artist_name = str(row.get("artist(s)_name", row.get("artist_name", ""))).strip()

        if not track_name:
            continue

        track_id = search_track(track_name, artist_name, token)
        if track_id:
            df.at[idx, "spotify_track_id"] = track_id
            image_url = get_track_album_image(track_id, token)
            if image_url:
                df.at[idx, "image_url"] = image_url
                enriched_count += 1

        # Small delay to respect rate limits
        time.sleep(0.05)

    df.to_csv(args.output, index=False)
    print(f"\n🎉 Completed! Enriched {enriched_count} tracks.")
    print(f"💾 Output saved to: {args.output}")


if __name__ == "__main__":
    main()
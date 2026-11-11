#!/usr/bin/env python
# coding: utf-8
"""自由選択ページの地図用に、海岸線を軽量なJSON（web/coast.json）へ書き出す（Natural Earth 50m・パブリックドメイン）。

地図タイル（外部サービス・APIキー）を使わず、Canvas で自前描画するための元データ。
形式: {"lines": [[lon0, lat0, lon1, lat1, ...], ...]}（経度は0〜360、小数1桁）。再生成は通常不要。
"""
import json
from pathlib import Path

import cartopy.feature as cfeature
from shapely.geometry import box

OUT = Path(__file__).resolve().parent / "web" / "coast.json"


def main():
    clip = box(-180, -20, 180, 90)
    lines = []
    for g in cfeature.COASTLINE.with_scale("50m").geometries():
        g = g.intersection(clip).simplify(0.06)
        for part in getattr(g, "geoms", [g]):
            if part.geom_type != "LineString" or len(part.coords) < 2:
                continue
            flat = []
            for lon, lat in part.coords:
                flat += [round(lon % 360.0, 1), round(lat, 1)]
            lines.append(flat)
    OUT.write_text(json.dumps({"lines": lines}, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT}: {len(lines)} 本, {OUT.stat().st_size / 1024:.0f}KB")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""tools/portrait_qc.py -- 顔絵の受け入れ検査（§7.59 / docs/design/portrait-brief.md）

    python3 tools/portrait_qc.py                # 全部を検査
    python3 tools/portrait_qc.py 徐盛 曹仁       # 指定の人物だけ

**小さくして生き残るかを測る。** 顔絵はカード128px・敵陣42px・チップ20px円まで
縮む。暗くて平坦な絵はチップで黒い塊になり、誰か分からなくなる（実際に初回納品の
10枚で起きた）。そこで中央正方形を20×20へ落とし、

    明るさ  = 平均の輝度（暗すぎないか）
    ばらつき = 輝度の標準偏差（**形が残っているか**）

を見る。実測の目安は **ばらつき35以上**（No.11-20 の改良版が 38.1、初回が 27.6）。
依存を増やさないため PNG は自前で展開する（Pillow を入れない）。
"""

from __future__ import annotations

import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTRAITS = os.path.join(HERE, "sim", "webui", "portraits")
SD_TARGET = 35.0          # 20px でのばらつきの目安（実測合わせ）
LUM_TARGET = 70.0         # 同・明るさの下限


def read_png(path: str):
    d = open(path, "rb").read()
    pos, w, h, ct, bd, idat, plte = 8, 0, 0, 0, 0, b"", b""
    while pos < len(d):
        ln = struct.unpack(">I", d[pos:pos + 4])[0]
        typ, body = d[pos + 4:pos + 8], d[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", body[:10])
        elif typ == b"PLTE":
            plte = body
        elif typ == b"IDAT":
            idat += body
        pos += 12 + ln
    if bd != 8:
        raise ValueError("8bit の PNG だけ扱う: " + path)
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    raw, stride = zlib.decompress(idat), w * ch
    out, prev, p = bytearray(h * stride), bytearray(stride), 0
    for y in range(h):
        f = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, ch, ct, plte, out


def crop_stats(path: str, where: str = "chip", n: int = 20):
    """切り抜き位置ごとに n×n へ落として (明るさ, ばらつき) を返す。

    where="chip": 中央正方形（登用チップ20px円・敵陣42px と同じ範囲）
    where="card": 上から64%（武将カードと同じ範囲）

    **この2つは別物である。** No.21-30 で、カード表示は明るいのにチップだけ
    暗い、という食い違いが出た — 明るい勢力色の面が頭上にしか無く、中央帯
    （肩から胸）が暗い装束で埋まっていたため。片方だけ測ると誤診する。
    """
    w, h, ch, ct, plte, px = read_png(path)
    if where == "card":
        x0, y0 = 0, 0
        cw, chh = w, int(h * 0.64)
    else:
        side = min(w, h)
        x0, y0 = (w - side) // 2, (h - side) // 2
        cw = chh = side
    acc = [[0.0] * n for _ in range(n)]
    cnt = [[0] * n for _ in range(n)]
    for y in range(y0, y0 + chh, 2):
        for x in range(x0, x0 + cw, 2):
            if ct == 3:
                i = px[y * w + x] * 3
                r, g, b = plte[i], plte[i + 1], plte[i + 2]
            else:
                i = (y * w + x) * ch
                r, g, b = px[i], px[i + 1], px[i + 2]
            cy, cx = (y - y0) * n // chh, (x - x0) * n // cw
            acc[cy][cx] += 0.299 * r + 0.587 * g + 0.114 * b
            cnt[cy][cx] += 1
    vals = [acc[r][c] / cnt[r][c] for r in range(n) for c in range(n) if cnt[r][c]]
    m = sum(vals) / len(vals)
    return m, (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5


def main(argv):
    names = argv[1:]
    files = ([os.path.join(PORTRAITS, n + ".png") for n in names] if names
             else sorted(os.path.join(PORTRAITS, f)
                         for f in os.listdir(PORTRAITS) if f.endswith(".png")))
    print("人物        寸法      カード(上64%)      チップ(中央20px)")
    print("                      明るさ ばらつき   明るさ ばらつき")
    bad = []
    for f in files:
        if not os.path.exists(f):
            print("  {} が無い".format(os.path.basename(f)))
            continue
        w, h, *_ = read_png(f)
        cm, csd = crop_stats(f, "card")
        m, sd = crop_stats(f, "chip")
        flag = "" if (sd >= SD_TARGET and m >= LUM_TARGET) else "  ← チップで沈む"
        if flag:
            bad.append(os.path.basename(f)[:-4])
        size = "{}x{}".format(w, h)
        if (w, h) != (480, 640):
            size += "!"
        print("  {:<10}{:<9}{:6.1f}{:8.1f}   {:6.1f}{:8.1f}{}".format(
            os.path.basename(f)[:-4], size, cm, csd, m, sd, flag))
    print("\n目安は**チップ側**: 明るさ {:.0f}以上・ばらつき {:.0f}以上"
          "（No.1-10 が 27.6、No.11-20 が 38.1）。"
          "\nカード側だけ明るくても、明るい色面が頭上にしか無いとチップで沈む。"
          .format(LUM_TARGET, SD_TARGET))
    if bad:
        print("要確認 {} 件: {}".format(len(bad), "・".join(bad)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

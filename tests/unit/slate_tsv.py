"""Canned tesseract TSV for slate unit tests (layout of the sample slate at 1920x1080:
right-aligned labels ending at x=308, values starting at x=370, 61 px line pitch).
`tsv(rows)` builds the 12-column output of `tesseract IMG stdout --psm 3 tsv`, levels 1-5
(only level 5 = word rows carry text)."""

HDR = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
FIELDS = [("AGENCY", "Northwind Advertising"), ("CLIENT", "Sample Burgers"), ("PRODUCT", ""),
          ("TITLE", "SB X League 2026_ProRes Generic Submaster_QB Pat_60_ABCD1234567H_1920x1080_Slated"),
          ("AD-ID", "ABCD1234567H"), ("DURATION", ":60"), ("AUDIO", "Stereo"), ("DATE", "20261026"),
          ("ARTIST", "Alex Example"), ("MISC", "edit-bay-03")]
CW, H = 11, 18       # char width, word height (px)


def words(rows=FIELDS, conf=None, extra=(), label_right=308, value_x=370, top=331, pitch=61):
    """-> [(text, conf, x, y, w, h)]; conf: {text: confidence} overrides (default 92)."""
    conf = conf or {}
    out = []
    for i, (label, value) in enumerate(rows):
        y = top + i * pitch
        lw = len(label) * CW
        out.append((label, conf.get(label, 95), label_right - lw, y, lw, H))
        x = value_x
        for wd in value.split():
            w = len(wd) * CW
            out.append((wd, conf.get(wd, 92), x, y, w, H))
            x += w + 6
    out.extend(extra)
    return out


def tsv(ws) -> str:
    lines = [HDR, "1\t1\t0\t0\t0\t0\t0\t0\t1920\t1080\t-1\t", "2\t1\t1\t0\t0\t0\t190\t320\t1200\t640\t-1\t"]
    for n, (text, c, x, y, w, h) in enumerate(ws, 1):
        lines.append(f"4\t1\t1\t1\t{n}\t0\t{x}\t{y}\t{w}\t{h}\t-1\t")
        lines.append(f"5\t1\t1\t1\t{n}\t1\t{x}\t{y}\t{w}\t{h}\t{c}\t{text}")
    return "\n".join(lines) + "\n"

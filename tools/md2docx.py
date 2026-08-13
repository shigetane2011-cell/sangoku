#!/usr/bin/env python3
"""仕様書の Markdown を docx へ変換する。

v0.1 の docx をテンプレートとして使い、styles.xml / numbering.xml / ヘッダー /
フッター / テーマをそのまま流用する。document.xml だけを生成し直すため、
出力は v0.1 と同じ見た目になる。

usage: python3 tools/md2docx.py <input.md> <template.docx> <output.docx>

対応する Markdown 記法（本仕様書で使う範囲に限定）:
  # / ## / ###      先頭のタイトルブロック（最初の --- まで）
  ## / ### / ####   本文中の見出し（Heading1 / Heading2 / Heading3）
  - key: value      タイトルブロック内のメタ行
  |  ...  |         表（1行目がヘッダー）
  - / 1.            箇条書き / 番号付き
  > **見出し** 本文  カラーの囲み（コールアウト）
  ```               等幅の囲み
  **bold**          太字（段落・表セル内とも）
"""

import re
import sys
import zipfile

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

FONT = '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Yu Gothic" w:cs="Calibri"/>'
MONO = '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:eastAsia="Yu Gothic" w:cs="Consolas"/>'

TABLE_W = 9360          # 本文幅（twips）
BORDER = 'CBD5E1'       # 表の罫線
HEAD_FILL = 'E8EEF5'    # 表ヘッダーの背景
ACCENT_FILL = 'E6F2FF'  # 「確定」セルとコールアウトの背景
CALLOUT_BORDER = 'D7DEE8'
CODE_FILL = 'F6F8FA'


def esc(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def run(text, *, bold=False, color='1F2937', size=22, mono=False):
    """1つの w:r を組み立てる。"""
    if not text:
        return ''
    rpr = (MONO if mono else FONT) + ('<w:b/>' if bold else '')
    rpr += f'<w:color w:val="{color}"/><w:sz w:val="{size}"/>'
    space = ' xml:space="preserve"' if text != text.strip() else ''
    return f'<w:r><w:rPr>{rpr}</w:rPr><w:t{space}>{esc(text)}</w:t></w:r>'


WARNINGS = []


def runs(text, *, color='1F2937', size=22, bold_all=False):
    """**bold** を解釈して w:r の並びを返す。

    太字は1行内で閉じる必要がある。行をまたぐと ** が本文にそのまま残るため、
    検出して警告する（黙って壊れると気づけない）。
    """
    if text.count('**') % 2:
        WARNINGS.append(f'閉じていない太字マーカー: {text[:60]}')
    out = []
    for i, part in enumerate(re.split(r'\*\*(.+?)\*\*', text)):
        if part:
            out.append(run(part, bold=bold_all or i % 2 == 1, color=color, size=size))
    return ''.join(out)


def para(content, *, style=None, spacing=None, numid=None, jc=None):
    ppr = ''
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
        if style.startswith('Heading'):
            ppr += '<w:keepNext/>'
    if spacing:
        ppr += spacing
    if jc:
        ppr += f'<w:jc w:val="{jc}"/>'
    if numid:
        ppr += f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{numid}"/></w:numPr>'
    return f'<w:p><w:pPr>{ppr}</w:pPr>{content}</w:p>'


def borders(color):
    edges = ''.join(
        f'<w:{e} w:val="single" w:sz="6" w:space="0" w:color="{color}"/>'
        for e in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV')
    )
    return f'<w:tblBorders>{edges}</w:tblBorders>'


def tbl_props(color, style=True):
    return (
        '<w:tblPr>'
        + ('<w:tblStyle w:val="TableGrid"/>' if style else '')
        + f'<w:tblW w:type="dxa" w:w="{TABLE_W}"/><w:jc w:val="left"/>'
        '<w:tblLayout w:type="fixed"/>'
        '<w:tblLook w:firstColumn="1" w:firstRow="1" w:lastColumn="0" w:lastRow="0"'
        ' w:noHBand="0" w:noVBand="1" w:val="04A0"/>'
        '<w:tblInd w:w="120" w:type="dxa"/>'
        + borders(color) + '</w:tblPr>'
    )


def cell(width, content, *, fill=None, valign='center', jc='left'):
    shd = f'<w:shd w:fill="{fill}" w:val="clear"/>' if fill else ''
    return (
        f'<w:tc><w:tcPr><w:tcW w:type="dxa" w:w="{width}"/>{shd}'
        f'<w:vAlign w:val="{valign}"/>'
        '<w:tcMar><w:top w:w="80" w:type="dxa"/><w:start w:w="120" w:type="dxa"/>'
        '<w:bottom w:w="80" w:type="dxa"/><w:end w:w="120" w:type="dxa"/></w:tcMar>'
        f'</w:tcPr>{content}</w:tc>'
    )


CELL_SPACING = '<w:spacing w:before="0" w:after="0" w:line="276" w:lineRule="auto"/>'


def column_widths(rows):
    """列の内容量から幅を按分する。"""
    ncols = len(rows[0])
    weights = []
    for c in range(ncols):
        longest = max(len(re.sub(r'\*\*', '', r[c])) for r in rows if c < len(r))
        weights.append(max(4, min(longest, 34)))
    total = sum(weights)
    widths = [max(760, int(TABLE_W * w / total)) for w in weights]
    widths[-1] += TABLE_W - sum(widths)   # 端数を最終列で吸収
    return widths


def build_table(rows):
    widths = column_widths(rows)
    head, body = rows[0], rows[1:]
    # v0.1 に倣い、「状態」列を持つ表では確定行のその列を強調する。
    # v0.1 では常に1列目だったが、2列目以降にある表でも同じ体裁にそろえる。
    status_col = next((i for i, h in enumerate(head) if h.strip() == '状態'), None)

    out = [tbl_props(BORDER)]
    out.append('<w:tblGrid>' + ''.join(f'<w:gridCol w:w="{w}"/>' for w in widths) + '</w:tblGrid>')

    cells = ''.join(
        cell(widths[i],
             para(runs(head[i], color='1F4D78', size=18, bold_all=True),
                  spacing=CELL_SPACING, jc='center' if i == status_col else 'left'),
             fill=HEAD_FILL)
        for i in range(len(head))
    )
    out.append(f'<w:tr><w:trPr><w:tblHeader w:val="true"/><w:cantSplit/></w:trPr>{cells}</w:tr>')

    for r in body:
        r = r + [''] * (len(widths) - len(r))
        cells = ''
        for i, text in enumerate(r[:len(widths)]):
            fill = None
            jc = 'left'
            if i == status_col:
                jc = 'center'
                if re.sub(r'\*\*', '', text).strip() in ('確定', '必須'):
                    fill = ACCENT_FILL
            cells += cell(widths[i],
                          para(runs(text, size=18), spacing=CELL_SPACING, jc=jc),
                          fill=fill)
        out.append(f'<w:tr><w:trPr><w:cantSplit/></w:trPr>{cells}</w:tr>')
    return '<w:tbl>' + ''.join(out) + '</w:tbl>' + para('', spacing='<w:spacing w:after="120"/>')


def build_callout(text):
    """> **見出し** 本文 を色付きの囲みにする。"""
    m = re.match(r'\*\*(.+?)\*\*\s*(.*)', text)
    if m:
        content = (run(m.group(1) + '  ', bold=True, color='1F4D78', size=21)
                   + runs(m.group(2), size=21))
    else:
        content = runs(text, size=21)
    inner = para(content, spacing='<w:spacing w:after="0" w:line="288" w:lineRule="auto"/>')
    return (
        '<w:tbl>' + tbl_props(CALLOUT_BORDER, style=False)
        + f'<w:tblGrid><w:gridCol w:w="{TABLE_W}"/></w:tblGrid>'
        + '<w:tr><w:trPr><w:cantSplit/></w:trPr>'
        + cell(TABLE_W, inner, fill=ACCENT_FILL) + '</w:tr></w:tbl>'
        + para('', spacing='<w:spacing w:after="120"/>')
    )


def build_code(lines):
    inner = ''.join(
        para(run(ln or ' ', color='1F2937', size=18, mono=True),
             spacing='<w:spacing w:before="0" w:after="0" w:line="264" w:lineRule="auto"/>')
        for ln in lines
    )
    return (
        '<w:tbl>' + tbl_props(CALLOUT_BORDER, style=False)
        + f'<w:tblGrid><w:gridCol w:w="{TABLE_W}"/></w:tblGrid>'
        + '<w:tr><w:trPr><w:cantSplit/></w:trPr>'
        + cell(TABLE_W, inner, fill=CODE_FILL, valign='top') + '</w:tr></w:tbl>'
        + para('', spacing='<w:spacing w:after="120"/>')
    )


def split_row(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def is_divider(cells):
    return all(re.fullmatch(r':?-{2,}:?', c) for c in cells if c)


def convert(md):
    """Markdown を document.xml の本文へ変換する。

    戻り値は (本文XML, 番号付きリスト用に新規採番した numId の一覧)。
    numbering.xml の numId 42 をそのまま使い回すと文書全体で連番になってしまう
    ため、リストごとに startOverride 付きの numId を割り当てる。
    """
    lines = md.split('\n')
    out = []
    i = 0
    front = True
    seen = {'h1': False, 'h2': False, 'h3': False}
    ordered_ids = []
    prev_ordered = False

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        # --- 区切り: タイトルブロックの終わり / 節の区切り
        if re.fullmatch(r'-{3,}', stripped):
            front = False
            prev_ordered = False
            out.append(para('', spacing='<w:spacing w:after="80"/>'))
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        # 空行はリストの連続性を壊さないので、判定はここで行う
        was_ordered, prev_ordered = prev_ordered, False

        # --- 等幅の囲み
        if stripped.startswith('```'):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith('```'):
                buf.append(lines[i].rstrip())
                i += 1
            i += 1
            out.append(build_code(buf))
            continue

        # --- 表
        if stripped.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                cells = split_row(lines[i])
                if not is_divider(cells):
                    rows.append(cells)
                i += 1
            if rows:
                out.append(build_table(rows))
            continue

        # --- コールアウト
        if stripped.startswith('>'):
            buf = [stripped.lstrip('>').strip()]
            i += 1
            while i < len(lines) and lines[i].strip().startswith('>'):
                buf.append(lines[i].strip().lstrip('>').strip())
                i += 1
            out.append(build_callout(' '.join(buf)))
            continue

        # --- 見出し
        m = re.match(r'(#{1,4})\s+(.*)', stripped)
        if m:
            level, text = len(m.group(1)), m.group(2)
            if front:
                # タイトルブロック: # → 小見出し, ## → 大見出し, ### → サブタイトル
                if level == 1 and not seen['h1']:
                    seen['h1'] = True
                    out.append(para(run(text, bold=True, color='2E74B5', size=20),
                                    spacing='<w:spacing w:before="160" w:after="40"/>'))
                elif level == 2 and not seen['h2']:
                    seen['h2'] = True
                    out.append(para(run(text, bold=True, color='111827', size=48),
                                    spacing='<w:spacing w:before="0" w:after="80"/>'))
                elif level == 3 and not seen['h3']:
                    seen['h3'] = True
                    out.append(para(run(text, color='667085', size=26),
                                    spacing='<w:spacing w:before="0" w:after="280"/>'))
                i += 1
                continue
            style, size = {2: ('Heading1', 32), 3: ('Heading2', 26), 4: ('Heading3', 23)}.get(
                level, ('Heading2', 26))
            out.append(para(run(text, bold=True, color='2E74B5', size=size), style=style))
            i += 1
            continue

        # --- 箇条書き / 番号付き
        m = re.match(r'[-*]\s+(.*)', stripped)
        if m:
            text = m.group(1)
            if front:
                # 「版: v0.2」のようなメタ行
                mk = re.match(r'([^:：]+[:：])\s*(.*)', text)
                sp = '<w:spacing w:before="0" w:after="40" w:line="252" w:lineRule="auto"/>'
                if mk:
                    out.append(para(run(mk.group(1) + ' ', bold=True, size=20)
                                    + runs(mk.group(2), size=20), spacing=sp))
                else:
                    out.append(para(runs(text, size=20), spacing=sp))
            else:
                out.append(para(runs(text),
                                spacing='<w:spacing w:before="0" w:after="80" w:line="300" w:lineRule="auto"/>',
                                numid=41))
            i += 1
            continue

        m = re.match(r'\d+\.\s+(.*)', stripped)
        if m and not front:
            if not was_ordered:
                ordered_ids.append(100 + len(ordered_ids))
            prev_ordered = True
            out.append(para(runs(m.group(1)),
                            spacing='<w:spacing w:before="0" w:after="80" w:line="300" w:lineRule="auto"/>',
                            numid=ordered_ids[-1]))
            i += 1
            continue

        # --- 「表記：」のような注記は SmallNote
        if stripped.startswith('**表記'):
            out.append(para(runs(stripped, color='667085', size=18), style='SmallNote'))
            i += 1
            continue

        # --- 通常の段落
        out.append(para(runs(stripped),
                        spacing='<w:spacing w:before="0" w:after="120" w:line="300" w:lineRule="auto"/>'))
        i += 1

    return ''.join(out), ordered_ids


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    md_path, template, out_path = sys.argv[1:4]

    with open(md_path, encoding='utf-8') as f:
        md = f.read()

    with zipfile.ZipFile(template) as z:
        original = z.read('word/document.xml').decode('utf-8')
        names = z.namelist()
        blobs = {n: z.read(n) for n in names}

    body, ordered_ids = convert(md)
    head = original[:original.index('<w:body>') + len('<w:body>')]
    tail = original[original.index('<w:sectPr'):]
    document = head + body + tail

    # 番号付きリストごとに 1 から振り直すため、numId を追加する
    if ordered_ids:
        numbering = blobs['word/numbering.xml'].decode('utf-8')
        added = ''.join(
            f'<w:num w:numId="{n}"><w:abstractNumId w:val="42"/>'
            '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>'
            for n in ordered_ids
        )
        numbering = numbering.replace('</w:numbering>', added + '</w:numbering>')
        blobs['word/numbering.xml'] = numbering.encode('utf-8')

    # docProps/core.xml のタイトルを合わせる（あれば）
    core = blobs.get('docProps/core.xml')
    if core:
        text = core.decode('utf-8')
        text = re.sub(r'(<dc:title>).*?(</dc:title>)',
                      r'\g<1>三国志 部隊対戦シミュレーション コンセプト仕様 v0.2\g<2>', text)
        blobs['docProps/core.xml'] = text.encode('utf-8')

    blobs['word/document.xml'] = document.encode('utf-8')

    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.writestr(n, blobs[n])

    print(f'wrote {out_path} ({len(document):,} bytes of document.xml)')
    for w in WARNINGS:
        print(f'  警告: {w}')
    if WARNINGS:
        sys.exit(f'{len(WARNINGS)} 件の警告があります。太字を1行内で閉じてください。')


if __name__ == '__main__':
    main()

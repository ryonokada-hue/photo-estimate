"""
見積書PDF生成モジュール
reportlab を使用して日本語対応の見積書PDFを作成する
"""

import os
import unicodedata
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# フォント設定
def _setup_font():
    """日本語フォントを見つけて登録する。なければダウンロードする。"""
    import glob
    import urllib.request

    # 候補パスを順に試す
    font_paths = [
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    # glob検索も追加
    for pattern in ["/usr/share/fonts/**/NotoSansCJK*.ttc",
                    "/usr/share/fonts/**/NotoSans*CJK*.otf",
                    "/usr/share/fonts/**/*CJK*.ttf",
                    "/usr/share/fonts/**/*gothic*.ttf",
                    "/usr/share/fonts/**/*Gothic*.ttf"]:
        font_paths.extend(glob.glob(pattern, recursive=True))

    for fp in font_paths:
        if os.path.exists(fp):
            try:
                if fp.endswith(".ttc"):
                    pdfmetrics.registerFont(TTFont("JaCJK", fp, subfontIndex=0))
                else:
                    pdfmetrics.registerFont(TTFont("JaCJK", fp))
                return "JaCJK"
            except Exception:
                continue

    # システムにフォントがない場合、NotoSansJPをダウンロード
    local_font = os.path.join(os.path.dirname(__file__), "NotoSansJP-Regular.ttf")
    if not os.path.exists(local_font):
        url = "https://github.com/google/fonts/raw/main/ofl/notosansjp/NotoSansJP%5Bwght%5D.ttf"
        try:
            urllib.request.urlretrieve(url, local_font)
        except Exception:
            # 別のURL
            url2 = "https://github.com/googlefonts/noto-cjk/raw/main/Sans/Variable/TTF/NotoSansCJKjp-VF.ttf"
            try:
                urllib.request.urlretrieve(url2, local_font)
            except Exception:
                return "Helvetica"  # 最終フォールバック

    try:
        pdfmetrics.registerFont(TTFont("JaCJK", local_font))
        return "JaCJK"
    except Exception:
        return "Helvetica"

JA_FONT = _setup_font()
EN_FONT = "Helvetica"   # ASCII/数字/記号用


def _is_cjk(ch):
    """文字がCJK（日本語等）かどうか判定"""
    cp = ord(ch)
    return (
        (0x2000 <= cp <= 0x206F) or   # 一般句読点（※等）
        (0x2100 <= cp <= 0x214F) or   # レターライクシンボル
        (0x2190 <= cp <= 0x21FF) or   # 矢印
        (0x2500 <= cp <= 0x257F) or   # 罫線素片
        (0x25A0 <= cp <= 0x25FF) or   # 幾何学模様
        (0x2600 <= cp <= 0x26FF) or   # その他記号
        (0x3000 <= cp <= 0x9FFF) or   # CJK統合、ひらがな、カタカナ、記号
        (0xF900 <= cp <= 0xFAFF) or   # CJK互換
        (0xFF00 <= cp <= 0xFFEF) or   # 全角ASCII、半角カタカナ
        (0x20000 <= cp <= 0x2FA1F)    # CJK拡張
    )


def _draw_mixed(c, x, y, text, size, align="left", max_x=None):
    """
    ASCII部分はHelvetica、CJK部分はJaCJKで描画する混在テキスト描画関数
    align: "left" | "right" | "center"
    """
    # セグメントに分割
    segments = []
    current = ""
    current_is_cjk = None

    for ch in text:
        is_cjk = _is_cjk(ch)
        if current_is_cjk is None:
            current_is_cjk = is_cjk
        if is_cjk != current_is_cjk:
            if current:
                segments.append((current, current_is_cjk))
            current = ch
            current_is_cjk = is_cjk
        else:
            current += ch
    if current:
        segments.append((current, current_is_cjk))

    # 合計幅を計算
    total_width = 0
    for seg_text, seg_is_cjk in segments:
        font = JA_FONT if seg_is_cjk else EN_FONT
        total_width += pdfmetrics.stringWidth(seg_text, font, size)

    # 開始x座標を決定
    if align == "right":
        start_x = x - total_width
    elif align == "center":
        start_x = x - total_width / 2
    else:
        start_x = x

    # 描画
    cur_x = start_x
    for seg_text, seg_is_cjk in segments:
        font = JA_FONT if seg_is_cjk else EN_FONT
        c.setFont(font, size)
        c.drawString(cur_x, y, seg_text)
        cur_x += pdfmetrics.stringWidth(seg_text, font, size)


def generate_estimate_pdf(
    output_path: str,
    client_name: str,
    estimate_date: str,
    company_name: str,
    subject: str,
    note: str,
    items: list[dict],
    total: int,
):
    width, height = A4
    c = canvas.Canvas(output_path, pagesize=A4)

    y = height - 30 * mm

    # タイトル
    _draw_mixed(c, width / 2, y, "御 見 積 書", 24, align="center")
    y -= 15 * mm

    # 区切り線
    c.setStrokeColor(colors.HexColor("#2563eb"))
    c.setLineWidth(2)
    c.line(20 * mm, y, width - 20 * mm, y)
    y -= 12 * mm

    # 宛名
    _draw_mixed(c, 25 * mm, y, client_name, 14)
    _draw_mixed(c, 25 * mm, y - 6 * mm, "下記の通りお見積もり申し上げます。", 10)

    # 日付・発行者（右側）
    try:
        dt = datetime.strptime(estimate_date, "%Y-%m-%d")
        date_str = f"見積日: {dt.year}年{dt.month}月{dt.day}日"
    except (ValueError, TypeError):
        date_str = "見積日: " + datetime.now().strftime("%Y年%m月%d日")

    right_x = width - 25 * mm
    _draw_mixed(c, right_x, y, date_str, 10, align="right")
    if company_name:
        _draw_mixed(c, right_x, y - 6 * mm, company_name, 10, align="right")

    y -= 18 * mm

    # 件名
    if subject:
        _draw_mixed(c, 25 * mm, y, f"件名: {subject}", 11)
        y -= 8 * mm

    # 合計金額ボックス
    c.setStrokeColor(colors.HexColor("#1d4ed8"))
    c.setFillColor(colors.HexColor("#eff6ff"))
    c.roundRect(25 * mm, y - 14 * mm, width - 50 * mm, 18 * mm, 3 * mm, fill=1, stroke=1)

    c.setFillColor(colors.HexColor("#1d4ed8"))
    _draw_mixed(c, 30 * mm, y - 4 * mm, "お見積もり金額（税抜）", 11)
    _draw_mixed(c, width - 30 * mm, y - 5 * mm, f"\u00a5{total:,}", 18, align="right")

    c.setFillColor(colors.black)
    y -= 25 * mm

    # テーブル（3列: 品目, カテゴリ, 数量）
    tl = 25 * mm
    tr = width - 25 * mm
    tw = tr - tl
    cw = [tw * 0.55, tw * 0.25, tw * 0.20]
    cx = [tl]
    for w in cw[:-1]:
        cx.append(cx[-1] + w)

    row_h = 8 * mm

    # ヘッダー
    c.setFillColor(colors.HexColor("#f3f4f6"))
    c.rect(tl, y - row_h, tw, row_h, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#d1d5db"))
    c.setLineWidth(0.5)
    c.line(tl, y, tr, y)
    c.line(tl, y - row_h, tr, y - row_h)

    c.setFillColor(colors.HexColor("#6b7280"))
    headers = ["品目", "カテゴリ", "数量"]
    for i, h in enumerate(headers):
        if i == 2:
            _draw_mixed(c, cx[i] + cw[i] - 2 * mm, y - 5.5 * mm, h, 8, align="right")
        else:
            _draw_mixed(c, cx[i] + 2 * mm, y - 5.5 * mm, h, 8)
    y -= row_h

    # データ行
    c.setFillColor(colors.black)
    for idx, item in enumerate(items):
        if item.get("quantity", 0) == 0:
            continue

        if y - row_h < 40 * mm:
            _draw_footer(c, width)
            c.showPage()
            y = height - 25 * mm
            c.setFillColor(colors.black)

        if idx % 2 == 0:
            c.setFillColor(colors.HexColor("#fafafa"))
            c.rect(tl, y - row_h, tw, row_h, fill=1, stroke=0)
            c.setFillColor(colors.black)

        c.setStrokeColor(colors.HexColor("#e5e7eb"))
        c.line(tl, y - row_h, tr, y - row_h)

        _draw_mixed(c, cx[0] + 2 * mm, y - 5.5 * mm, str(item.get("item_name", "")), 9)
        _draw_mixed(c, cx[1] + 2 * mm, y - 5.5 * mm, str(item.get("category", "")), 9)
        _draw_mixed(c, cx[2] + cw[2] - 2 * mm, y - 5.5 * mm, str(item.get("quantity", "")), 9, align="right")

        y -= row_h

    # 合計行
    c.setStrokeColor(colors.HexColor("#374151"))
    c.setLineWidth(1.5)
    c.line(tl, y, tr, y)
    y -= row_h

    _draw_mixed(c, cx[1] + cw[1] - 2 * mm, y + 2.5 * mm, "合計（税抜）", 11, align="right")
    c.setFillColor(colors.HexColor("#1d4ed8"))
    _draw_mixed(c, cx[2] + cw[2] - 2 * mm, y + 2.5 * mm, f"\u00a5{total:,}", 12, align="right")

    c.setFillColor(colors.black)
    y -= 10 * mm

    # 備考
    if note:
        c.setFillColor(colors.HexColor("#6b7280"))
        _draw_mixed(c, 25 * mm, y, "【備考】", 9)
        c.setFillColor(colors.black)
        y -= 5 * mm
        for line in note.split("\n"):
            _draw_mixed(c, 25 * mm, y, line, 9)
            y -= 5 * mm

    # 注意書き
    y -= 5 * mm
    c.setFillColor(colors.HexColor("#9ca3af"))
    _draw_mixed(c, 25 * mm, y, "※ 本見積書はAIによる自動判定に基づく概算です。正式な見積もりは現地確認後にお出しいたします。", 7)
    y -= 4 * mm
    _draw_mixed(c, 25 * mm, y, "※ 見積有効期限: 発行日より30日間", 7)

    _draw_footer(c, width)
    c.save()
    return output_path


def _draw_footer(c, width):
    c.setFillColor(colors.HexColor("#d1d5db"))
    _draw_mixed(c, width / 2, 10 * mm, "写真見積もりシステムにより自動生成", 7, align="center")


if __name__ == "__main__":
    sample_items = [
        {"item_name": "冷蔵庫（大）", "category": "家電", "quantity": 1, "unit_price": 20500, "subtotal": 20500},
        {"item_name": "洗濯機", "category": "家電", "quantity": 1, "unit_price": 8200, "subtotal": 8200},
        {"item_name": "テーブル", "category": "家具", "quantity": 1, "unit_price": 4100, "subtotal": 4100},
        {"item_name": "椅e��", "category": "家具", "quantity": 4, "unit_price": 3075, "subtotal": 12300},
        {"item_name": "テレビ", "category": "家電", "quantity": 1, "unit_price": 6000, "subtotal": 6000},
        {"item_name": "テレビ台", "category": "家具", "quantity": 1, "unit_price": 8200, "subtotal": 8200},
        {"item_name": "カラーボックス", "category": "家具", "quantity": 2, "unit_price": 3075, "subtotal": 6150},
        {"item_name": "段ボール", "category": "その他", "quantity": 5, "unit_price": 2100, "subtotal": 10500},
        {"item_name": "布団", "category": "その他", "quantity": 1, "unit_price": 5125, "subtotal": 5125},
        {"item_name": "エアコン", "category": "家電", "quantity": 1, "unit_price": 6150, "subtotal": 6150},
    ]

    output = generate_estimate_pdf(
        output_path="sample_estimate.pdf",
        client_name="山田 太郎 様",
        estimate_date="2026-04-03",
        company_name="片付けサポート関西",
        subject="家財処分",
        note="2階作業、エレベーター無し\n駐車スペースあり",
        items=sample_items,
        total=87225,
    )
    print(f"PDF generated: {output}")

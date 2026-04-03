"""
引越し・不用品 写真見積もりアプリ（プロトタイプ）
- 写真をアップロードすると Claude Vision API で物体を識別
- CSVの単価データベースと照合して見積もりを自動計算
"""

import os
import csv
import json
import io
import base64
import tempfile
import logging
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
import anthropic
from PIL import Image
from generate_pdf import generate_estimate_pdf

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static")
CORS(app)

# ============================================================
# 設定
# ============================================================
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CSV_PATH = os.path.join(os.path.dirname(__file__), "price_database.csv")
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ============================================================
# 単価データベース読み込み
# ============================================================
def load_price_database():
    """CSVから単価データベースを読み込む"""
    items = []
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            items.append({
                "category": row["category"],
                "item_name": row["item_name"],
                "item_name_en": row["item_name_en"],
                "size": row["size"],
                "unit_price": int(row["unit_price"]),
                "notes": row["notes"],
            })
    return items


PRICE_DB = load_price_database()


def get_item_list_for_prompt():
    """プロンプトに渡す品目リストを生成"""
    lines = []
    for item in PRICE_DB:
        lines.append(f"- {item['item_name']}（{item['item_name_en']}）[{item['category']}]")
    return "\n".join(lines)


# ============================================================
# 画像リサイズ（iPhoneの大きな写真を縮小してAPI負荷を軽減）
# ============================================================
MAX_IMAGE_DIMENSION = 1568
MAX_IMAGE_BYTES = 3 * 1024 * 1024


def resize_image_if_needed(image_data, content_type):
    try:
        img = Image.open(io.BytesIO(image_data))
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
        w, h = img.size
        needs_resize = (w > MAX_IMAGE_DIMENSION or h > MAX_IMAGE_DIMENSION
                        or len(image_data) > MAX_IMAGE_BYTES)
        if not needs_resize:
            logger.info(f"画像リサイズ不要: {w}x{h}, {len(image_data)/1024:.0f}KB")
            return image_data, content_type
        ratio = min(MAX_IMAGE_DIMENSION / w, MAX_IMAGE_DIMENSION / h)
        if ratio < 1:
            new_w = int(w * ratio)
            new_h = int(h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            logger.info(f"画像リサイズ: {w}x{h} -> {new_w}x{new_h}")
        buf = io.BytesIO()
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=85, optimize=True)
        result = buf.getvalue()
        logger.info(f"画像圧縮: {len(image_data)/1024:.0f}KB -> {len(result)/1024:.0f}KB")
        return result, "image/jpeg"
    except Exception as e:
        logger.warning(f"画像リサイズ失敗: {e}")
        return image_data, content_type


# ============================================================
# Claude Vision API で画像を解析
# ============================================================
def analyze_image_with_claude(image_base64, media_type):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    item_list = get_item_list_for_prompt()
    system_prompt = f"""あなたは引越し・不用品回収の見積もりアシスタントです。
写真に写っている家具・家電・その他の品物を正確に識別してください。

## 識別可能な品目リスト（item_name は必ずこの表記に合わせること）
{item_list}

## サイズ判定の基準
- 冷蔵庫: 小=1ドア/ミニ冷蔵庫, 中=2ドア/一般家庭用, 大=3ドア以上/大型ファミリー
- 食器棚・棚・たんす: 中=高さ1m以下, 大=高さ1m以上
- テレビ: 通常=40インチ以下, 大=40インチ超
- ベッド: S=シングル, SD=セミダブル, D=ダブル（幅で判断）
- ソファ: 座面の幅・クッション数で1人用/2人用/3人用を判断

## 重要なルール
1. 写真に写っている物だけを報告してください。見えない物を推測で追加しないでください。
2. 同じ物が複数ある場合は quantity で数を示してください。
3. リストにない物でも引越し荷物になりうる物は item_name に具体的な名前を入れてください。
4. 壁・床・天井・ドア・窓など建物の構造物は無視してください。
5. confidence は high / medium / low のいずれかで判定の確信度を示してください。
6. 写真が暗い・ぼやけている場合でも、見える範囲で最善の判断をしてください。
7. 段ボール箱が複数ある場合は、可能な限り個数を数えてください。
8. 洋服がハンガーラックや山積みで見える場合は「洋服」として quantity=1 で報告してください。

## 出力フォーマット
以下のJSON配列のみを返してください。説明文や前置きは一切不要です。
[
  {{"item_name": "品目名", "quantity": 数量, "confidence": "high/medium/low"}}
]"""
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_base64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "この写真に写っている家具・家電・荷物をすべて識別してJSON形式で返してください。",
                    },
                ],
            }
        ],
        system=system_prompt,
    )
    raw_text = response.content[0].text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1]
        raw_text = raw_text.rsplit("```", 1)[0].strip()
    detected_items = json.loads(raw_text)
    return detected_items


# ============================================================
# 見積もり計算
# ============================================================
def calculate_estimate(detected_items):
    line_items = []
    total = 0
    unmatched = []
    for det in detected_items:
        name = det["item_name"]
        qty = det.get("quantity", 1)
        confidence = det.get("confidence", "medium")
        matched = None
        for db_item in PRICE_DB:
            if db_item["item_name"] == name:
                matched = db_item
                break
        if not matched:
            for db_item in PRICE_DB:
                if name in db_item["item_name"] or db_item["item_name"] in name:
                    matched = db_item
                    break
        if not matched:
            name_lower = name.lower()
            for db_item in PRICE_DB:
                if name_lower in db_item["item_name_en"].lower() or db_item["item_name_en"].lower() in name_lower:
                    matched = db_item
                    break
        if matched:
            subtotal = matched["unit_price"] * qty
            total += subtotal
            line_items.append({
                "item_name": matched["item_name"],
                "category": matched["category"],
                "size": matched["size"],
                "unit_price": matched["unit_price"],
                "quantity": qty,
                "subtotal": subtotal,
                "confidence": confidence,
                "notes": matched["notes"],
            })
        else:
            estimated_price = 3000
            subtotal = estimated_price * qty
            total += subtotal
            unmatched.append(name)
            line_items.append({
                "item_name": name,
                "category": "その他（未登録）",
                "size": "-",
                "unit_price": estimated_price,
                "quantity": qty,
                "subtotal": subtotal,
                "confidence": confidence,
                "notes": "単価DBに未登録のため仮単価",
            })
    return {
        "line_items": line_items,
        "total": total,
        "item_count": sum(item["quantity"] for item in line_items),
        "unmatched_items": unmatched,
    }


# ============================================================
# APIエンドポイント
# ============================================================
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/estimate", methods=["POST"])
def estimate():
    if "image" not in request.files:
        return jsonify({"error": "画像ファイルが必要です"}), 400
    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "ファイルが選択されていません"}), 400
    image_data = file.read()
    logger.info(f"画像受信: {file.filename}, サイズ={len(image_data)/1024:.0f}KB")
    content_type = file.content_type or "image/jpeg"
    if content_type not in ["image/jpeg", "image/png", "image/gif", "image/webp"]:
        return jsonify({"error": "対応画像形式: JPEG, PNG, GIF, WebP"}), 400
    try:
        image_data, content_type = resize_image_if_needed(image_data, content_type)
        image_base64 = base64.b64encode(image_data).decode("utf-8")
        logger.info(f"API送信サイズ: {len(image_base64)/1024:.0f}KB (base64)")
        logger.info("Claude Vision API 呼び出し開始...")
        detected_items = analyze_image_with_claude(image_base64, content_type)
        logger.info(f"識別結果: {len(detected_items)}品目検出")
        estimate_result = calculate_estimate(detected_items)
        return jsonify({
            "success": True,
            "detected_items": detected_items,
            "estimate": estimate_result,
        })
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析エラー: {e}")
        return jsonify({"error": f"AIの応答を解析できませんでした: {str(e)}"}), 500
    except anthropic.APIError as e:
        logger.error(f"Anthropic APIエラー: {e}")
        return jsonify({"error": f"Claude API エラー: {str(e)}"}), 500
    except Exception as e:
        logger.error(f"予期しないエラー: {e}", exc_info=True)
        return jsonify({"error": f"予期しないエラー: {str(e)}"}), 500


@app.route("/api/database", methods=["GET"])
def get_database():
    return jsonify({"items": PRICE_DB})


@app.route("/api/pdf", methods=["POST"])
def generate_pdf():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSONデータが必要です"}), 400
    client_name = data.get("client_name", "")
    if not client_name:
        return jsonify({"error": "宛名を入力してください"}), 400
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        generate_estimate_pdf(
            output_path=tmp_path,
            client_name=client_name,
            estimate_date=data.get("estimate_date", ""),
            company_name=data.get("company_name", "片付けサポート関西"),
            subject=data.get("subject", "家財処分"),
            note=data.get("note", ""),
            items=data.get("items", []),
            total=data.get("total", 0),
        )
        return send_file(
            tmp_path,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"見積書_{client_name}.pdf",
        )
    except Exception as e:
        return jsonify({"error": f"PDF生成エラー: {str(e)}"}), 500


# ============================================================
# 起動
# ============================================================
if __name__ == "__main__":
    if not ANTHROPIC_API_KEY:
        print("環境変数 ANTHROPIC_API_KEY を設定してください")
        print("   export ANTHROPIC_API_KEY=sk-ant-...")
    print("見積もりアプリ起動中: http://localhost:5000")
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", debug=debug, port=port)

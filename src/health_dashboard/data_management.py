"""利用者の追加と、日報・セルフケアシート・欠席情報のアップロード（画面に依存しない部分）。

エクスプローラーでフォルダを作ってファイルを入れる代わりに、アプリの画面から同じことが
できるようにする。保存先は従来と同じ data/<利用者名>/ で、ファイル名は「日報.xlsx」
「セルフケアシート.xlsx」「欠席.xlsx」に揃える（名前に「日報」「セルフケア」「欠席」を含む
xlsxを見分ける既存の仕組みに合う）。

アップロードされたファイルは、読み込めることを確かめてから、古いファイルと置き換える。
別の種類のファイルを間違えて入れても、今ある正しいデータが消えないようにするため。
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from health_dashboard.data_loader import (
    ABSENCE_KEYWORD,
    DAILY_REPORT_KEYWORD,
    SELFCARE_KEYWORD,
    find_absence_file,
    find_user_files,
    list_user_dirs,
    load_absence_records,
    load_daily_reports,
    load_selfcare_points,
)

MAX_USER_NAME_LENGTH = 40
_FORBIDDEN_NAME_CHARS = set('\\/:*?"<>|')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}  # fmt: skip
_TEMP_FILE_NAME = ".upload-tmp.xlsx"


@dataclass(frozen=True)
class UploadKind:
    key: str
    label: str  # 画面に表示する名前
    keyword: str  # ファイル名に含めて、種類を見分ける文字列
    filename: str  # 保存するときのファイル名


DAILY = UploadKind("daily", "日報", DAILY_REPORT_KEYWORD, "日報.xlsx")
SELFCARE = UploadKind("selfcare", "セルフケアシート", SELFCARE_KEYWORD, "セルフケアシート.xlsx")
ABSENCE = UploadKind("absence", "欠席情報", ABSENCE_KEYWORD, "欠席.xlsx")
UPLOAD_KINDS = (DAILY, SELFCARE, ABSENCE)


def validate_user_name(name: str, existing: list[str]) -> tuple[str | None, str | None]:
    """利用者名を検証する。戻り値は (整えた名前, エラーメッセージ)。どちらか一方がNone。"""
    cleaned = name.strip()
    if not cleaned:
        return None, "利用者の名前を入力してください。"
    if len(cleaned) > MAX_USER_NAME_LENGTH:
        return None, f"名前は{MAX_USER_NAME_LENGTH}文字以内にしてください。"
    if any(ch in _FORBIDDEN_NAME_CHARS for ch in cleaned) or any(ord(ch) < 32 for ch in cleaned):
        return None, "名前に使えない文字（ \\ / : * ? \" < > | ）が含まれています。"
    if cleaned.startswith(".") or cleaned.endswith((".", " ")):
        return None, "名前の先頭や末尾に「.」は使えません。"
    if cleaned.upper().split(".")[0] in _RESERVED_WINDOWS_NAMES:
        return None, "この名前は使えません。別の名前にしてください。"
    if cleaned.casefold() in {e.casefold() for e in existing}:
        return None, f"「{cleaned}」という利用者はすでにいます。"
    return cleaned, None


def create_user(data_dir: Path, name: str) -> tuple[Path | None, str | None]:
    """data_dirの下に、利用者のフォルダを作る。戻り値は (作ったフォルダ, エラーメッセージ)。"""
    existing = [p.name for p in list_user_dirs(data_dir)]
    cleaned, error = validate_user_name(name, existing)
    if error is not None:
        return None, error
    user_dir = data_dir / cleaned
    try:
        user_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return None, f"フォルダを作れませんでした（{exc.strerror or '原因不明'}）。"
    return user_dir, None


def _force_writable(function, path, _exc_info) -> None:
    """読み取り専用のファイルがあっても消せるようにする（Windowsで削除に失敗する原因）。"""
    os.chmod(path, stat.S_IWRITE)
    function(path)


def delete_user(data_dir: Path, user_dir: Path) -> str | None:
    """利用者のフォルダを、中のデータごと完全に削除する。成功ならNone、失敗ならエラーメッセージ。

    元に戻せない（ごみ箱には入らない）。data_dirの直下にある利用者のフォルダ以外は消さない。
    """
    if user_dir.is_symlink() or not user_dir.is_dir():
        return "この利用者のフォルダが見つかりません。"
    if user_dir.resolve().parent != data_dir.resolve() or user_dir.name.startswith("."):
        return "この利用者は削除できません。"
    # onerror は Python 3.12 から非推奨で、後継の onexc に置き換わっている。
    handler = {"onexc" if sys.version_info >= (3, 12) else "onerror": _force_writable}
    try:
        shutil.rmtree(user_dir, **handler)
    except OSError as exc:
        return (
            f"削除しきれませんでした（{exc.strerror or '原因不明'}）。"
            "Excelでこの利用者のファイルを開いている場合は閉じてから、もう一度試してください。"
        )
    return None


def _validate(kind: UploadKind, path: Path) -> None:
    """アップロードされたファイルが、その種類として読めることを確かめる。読めなければ例外。"""
    if kind is DAILY:
        load_daily_reports(path)
    elif kind is SELFCARE:
        if load_selfcare_points(path).empty:
            raise ValueError("チェックの記録が見つかりません")
    else:
        load_absence_records(path)


def _failure_message(kind: UploadKind, exc: Exception) -> str:
    reason = str(exc).split(":")[0].strip() if isinstance(exc, ValueError) else ""
    detail = f"（{reason}）" if reason else ""
    return (
        f"{kind.label}として読み込めませんでした{detail}。"
        "ファイルの種類が違わないか、Excel形式（.xlsx）か確認してください。"
        "今までのデータは、そのまま残しています。"
    )


def save_uploaded_file(user_dir: Path, kind: UploadKind, content: bytes) -> str | None:
    """アップロードされたファイルを検証し、古い同じ種類のファイルと置き換える。

    成功ならNone、失敗ならエラーメッセージを返す。失敗した場合、元のファイルには触れない。
    """
    temp_path = user_dir / _TEMP_FILE_NAME
    try:
        temp_path.write_bytes(content)
        try:
            _validate(kind, temp_path)
        except Exception as exc:  # noqa: BLE001 - 壊れたExcelなど、読めない理由は様々
            return _failure_message(kind, exc)

        for old in user_dir.glob("*.xlsx"):
            if old.name != _TEMP_FILE_NAME and kind.keyword in old.name:
                old.unlink()
        temp_path.replace(user_dir / kind.filename)
        return None
    except OSError as exc:
        return (
            f"{kind.label}を保存できませんでした（{exc.strerror or '原因不明'}）。"
            "Excelで開いている場合は閉じてから、もう一度試してください。"
        )
    finally:
        temp_path.unlink(missing_ok=True)


def file_status(user_dir: Path) -> dict[str, tuple[Path, dt.datetime] | None]:
    """種類ごとの、登録済みファイルとその更新日時（無ければNone）。"""
    daily, selfcare = find_user_files(user_dir)
    found = {DAILY.key: daily, SELFCARE.key: selfcare, ABSENCE.key: find_absence_file(user_dir)}
    return {
        key: (path, dt.datetime.fromtimestamp(path.stat().st_mtime)) if path else None
        for key, path in found.items()
    }

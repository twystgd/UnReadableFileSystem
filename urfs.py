#!/usr/bin/env python3
import argparse
import csv
import json
import os
import sqlite3
import struct
import sys
import zlib
from pathlib import Path
from typing import Any, Dict, List

MAGIC = b"URFS"
VERSION = 2

# Флаги суперблока
FLAG_COMPRESSED = 0x01
FLAG_NATIVE_BIN = 0x02


class URFSCompiler:
    """Компилятор и декомпилятор URFS (UnReadableFileSystem)."""

    @staticmethod
    def _bytes_to_bits(data: bytes) -> str:
        return "".join(f"{byte:08b}" for byte in data)

    @staticmethod
    def _bits_to_bytes(bits: str) -> bytes:
        clean_bits = "".join(ch for ch in bits if ch in ("0", "1"))
        if len(clean_bits) % 8 != 0:
            raise ValueError("Поврежденный образ URFS: количество бит не кратно 8.")
        return bytes(
            int(clean_bits[i : i + 8], 2) for i in range(0, len(clean_bits), 8)
        )

    # -------------------- ЗАГРУЗЧИКИ ИСТОЧНИКОВ --------------------

    @staticmethod
    def load_json(path: Path) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"t": "json", "n": path.name, "p": data}

    @staticmethod
    def load_sqlite(path: Path) -> Dict[str, Any]:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        tables: Dict[str, Any] = {}
        cursor.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
        )
        for row in cursor.fetchall():
            t_name = row["name"]
            cursor.execute(f'SELECT * FROM "{t_name}"')
            tables[t_name] = {
                "s": row["sql"],
                "r": [dict(r) for r in cursor.fetchall()],
            }
        conn.close()
        return {"t": "sqlite_db", "n": path.name, "p": tables}

    @staticmethod
    def load_sql_dump(path: Path) -> Dict[str, Any]:
        sql_script = path.read_text(encoding="utf-8")
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.executescript(sql_script)

        tables: Dict[str, Any] = {}
        cursor.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
        )
        for row in cursor.fetchall():
            t_name = row["name"]
            cursor.execute(f'SELECT * FROM "{t_name}"')
            tables[t_name] = {
                "s": row["sql"],
                "r": [dict(r) for r in cursor.fetchall()],
            }
        conn.close()
        return {"t": "sql_dump", "n": path.name, "p": tables}

    @staticmethod
    def load_csv(path: Path, delimiter: str = ",") -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            rows = list(reader)
            fieldnames = reader.fieldnames or []
        return {
            "t": "csv_table",
            "n": path.name,
            "p": {"c": list(fieldnames), "r": rows},
        }

    def ingest_file(self, filepath: str) -> Dict[str, Any]:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Файл не найден: {filepath}")

        ext = path.suffix.lower()
        if ext == ".json":
            return self.load_json(path)
        elif ext in (".db", ".sqlite", ".sqlite3"):
            return self.load_sqlite(path)
        elif ext == ".sql":
            return self.load_sql_dump(path)
        elif ext == ".csv":
            return self.load_csv(path, delimiter=",")
        elif ext == ".tsv":
            return self.load_csv(path, delimiter="\t")
        else:
            return {"t": "raw_blob", "n": path.name, "p": path.read_bytes().hex()}

    # -------------------- УПАКОВКА (PACK) --------------------

    def compile(
        self,
        inputs: List[str],
        output_path: str,
        mode: str = "native",
        compress: bool = False,
    ) -> None:
        inodes = [self.ingest_file(inp) for inp in inputs]

        encoded_blocks: List[bytes] = []
        inode_dir: List[Dict[str, Any]] = []

        for idx, inode in enumerate(inodes):
            raw_block = json.dumps(
                inode["p"], ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            if compress:
                raw_block = zlib.compress(raw_block, level=9)

            crc = zlib.crc32(raw_block) & 0xFFFFFFFF
            framed_block = struct.pack("!II", len(raw_block), crc) + raw_block
            encoded_blocks.append(framed_block)

            inode_dir.append(
                {
                    "i": idx,
                    "n": inode["n"],
                    "t": inode["t"],
                    "z": int(compress),
                }
            )

        dir_bytes = json.dumps(
            inode_dir, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if compress:
            dir_bytes = zlib.compress(dir_bytes, level=9)

        dir_crc = zlib.crc32(dir_bytes) & 0xFFFFFFFF
        framed_dir = struct.pack("!II", len(dir_bytes), dir_crc) + dir_bytes

        flags = 0
        if compress:
            flags |= FLAG_COMPRESSED
        if mode == "native":
            flags |= FLAG_NATIVE_BIN

        superblock = struct.pack("!4sBBH", MAGIC, VERSION, flags, len(inodes))
        all_sectors = [superblock, framed_dir] + encoded_blocks

        if mode == "native":
            with open(output_path, "wb") as out:
                for sector in all_sectors:
                    out.write(sector + b"\n")
        else:
            with open(output_path, "w", encoding="ascii", newline="\n") as out:
                if mode == "block":
                    for sector in all_sectors:
                        out.write(self._bytes_to_bits(sector) + "\n")
                elif mode == "byte":
                    full_stream = b"".join(all_sectors)
                    for byte in full_stream:
                        out.write(f"{byte:08b}\n")
                elif mode == "bit":
                    full_stream = b"".join(all_sectors)
                    bits = self._bytes_to_bits(full_stream)
                    out.write("\n".join(bits) + "\n")
                else:
                    raise ValueError(f"Неизвестный режим: {mode}")

        size_bytes = Path(output_path).stat().st_size
        size_kb = size_bytes / 1024
        print(
            f"[URFS] Упаковано в '{output_path}' ({size_bytes} Б / {size_kb:.2f} КиБ) | "
            f"Инодов: {len(inodes)} | Режим: {mode} | Сжатие: {compress}"
        )

    # -------------------- РАСПАКОВКА (UNPACK) --------------------

    @staticmethod
    def _skip_newline(stream: bytes, offset: int, context: str) -> int:
        """Безопасно пропускает разделитель строки (\n или \r\n) в нативном режиме."""
        if offset < len(stream) and stream[offset : offset + 2] == b"\r\n":
            return offset + 2
        if offset < len(stream) and stream[offset : offset + 1] == b"\n":
            return offset + 1
        if b"\xef\xbf\xbd" in stream:
            raise ValueError(
                f"Файл поврежден текстовым редактором (найдены символы замены UTF-8 'EF BF BD' в районе {context}). "
                "Пересобери файл через 'py urfs.py pack ...' и не сохраняй нативный .urfs в Блокноте!"
            )
        raise ValueError(
            f"Ожидался разделитель новой строки (\\n) после {context} "
            f"(найден байт: {stream[offset:offset+1]!r})."
        )

    def decompile(self, urfs_path: str, output_dir: str) -> None:
        raw_bytes = Path(urfs_path).read_bytes()

        # Автоопределение: начинается ли файл с нативных байт b'URFS'
        is_native = raw_bytes.startswith(MAGIC)

        if is_native:
            stream = raw_bytes
        else:
            stream = self._bits_to_bytes(raw_bytes.decode("ascii", errors="ignore"))

        offset = 0
        if len(stream) < 8:
            raise ValueError("Файл слишком мал для суперблока URFS.")

        magic, version, flags, inode_count = struct.unpack(
            "!4sBBH", stream[offset : offset + 8]
        )
        offset += 8

        if magic != MAGIC:
            raise ValueError(f"Неверная сигнатура файла: {magic!r} (ожидалась b'URFS')")

        if is_native:
            offset = self._skip_newline(stream, offset, "суперблока")

        # Читаем таблицу инодов
        dir_len, dir_crc = struct.unpack("!II", stream[offset : offset + 8])
        offset += 8
        dir_bytes = stream[offset : offset + dir_len]
        offset += dir_len

        if is_native:
            offset = self._skip_newline(stream, offset, "таблицы инодов")

        if (zlib.crc32(dir_bytes) & 0xFFFFFFFF) != dir_crc:
            raise ValueError(
                "Ошибка CRC32 при чтении таблицы инодов! "
                "Вероятно, файл .urfs был открыт и пересохранен в текстовом редакторе."
            )

        if version >= 2 and (flags & FLAG_COMPRESSED):
            dir_bytes = zlib.decompress(dir_bytes)

        directory = json.loads(dir_bytes.decode("utf-8"))
        out_root = Path(output_dir)
        out_root.mkdir(parents=True, exist_ok=True)

        for meta in directory:
            name = meta.get("n") or meta.get("name")
            itype = meta.get("t") or meta.get("type")
            is_compressed = bool(meta.get("z") or meta.get("compressed"))

            block_len, block_crc = struct.unpack("!II", stream[offset : offset + 8])
            offset += 8
            block_data = stream[offset : offset + block_len]
            offset += block_len

            if is_native:
                offset = self._skip_newline(stream, offset, f"блока {name}")

            if (zlib.crc32(block_data) & 0xFFFFFFFF) != block_crc:
                raise ValueError(f"Ошибка CRC32 в иноде {name}!")

            if is_compressed:
                block_data = zlib.decompress(block_data)

            payload = json.loads(block_data.decode("utf-8"))
            target_path = out_root / name
            self._restore_inode(itype, payload, target_path)
            print(f"[URFS] Восстановлен: {target_path} ({itype})")

    @staticmethod
    def _restore_inode(inode_type: str, payload: Any, target_path: Path) -> None:
        if inode_type == "json":
            target_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        elif inode_type in ("sqlite_db", "sql_dump"):
            db_path = (
                target_path.with_suffix(".db")
                if inode_type == "sql_dump"
                else target_path
            )
            if db_path.exists():
                db_path.unlink()
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            for table_name, table_data in payload.items():
                schema_sql = table_data.get("s") or table_data.get("schema")
                rows = table_data.get("r") or table_data.get("rows") or []
                cursor.execute(schema_sql)
                if rows:
                    cols = list(rows[0].keys())
                    placeholders = ", ".join(["?"] * len(cols))
                    col_names = ", ".join([f'"{c}"' for c in cols])
                    sql = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
                    for r in rows:
                        cursor.execute(sql, [r[c] for c in cols])
            conn.commit()
            conn.close()
        elif inode_type == "csv_table":
            cols = payload.get("c") or payload.get("columns") or []
            rows = payload.get("r") or payload.get("rows") or []
            with open(target_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=cols)
                writer.writeheader()
                writer.writerows(rows)
        elif inode_type == "raw_blob":
            target_path.write_bytes(bytes.fromhex(payload))


# -------------------- РЕГИСТРАЦИЯ В РЕЕСТРЕ WINDOWS --------------------


def register_in_windows_registry() -> None:
    """Регистрирует расширение .urfs и тип UnReadableFileSystem в реестре Windows."""
    if os.name != "nt":
        print("[URFS] Ошибка: Регистрация в реестре доступна только на Windows.")
        sys.exit(1)

    import ctypes
    import winreg

    # Если запущено как скомпилированный .exe (PyInstaller)
    if getattr(sys, "frozen", False):
        exe_path = str(Path(sys.executable).resolve())
        unpack_cmd = f'"{exe_path}" unpack "%1" -d "%1_extracted"'
    else:
        # Если запущено как обычный .py скрипт
        python_exe = sys.executable
        script_path = str(Path(__file__).resolve())
        unpack_cmd = f'"{python_exe}" "{script_path}" unpack "%1" -d "%1_extracted"'
    prog_id = "UnReadableFileSystem"

    # 1. Привязываем расширение .urfs к ProgID
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.urfs") as key:
        winreg.SetValue(key, "", winreg.REG_SZ, prog_id)

    # 2. Задаем отображаемое имя типа в Проводнике: "UnReadableFileSystem"
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}"
    ) as key:
        winreg.SetValue(key, "", winreg.REG_SZ, "UnReadableFileSystem")
        winreg.SetValueEx(
            key, "FriendlyTypeName", 0, winreg.REG_SZ, "UnReadableFileSystem"
        )

    # 3. Устанавливаем системную иконку из imageres.dll
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}\DefaultIcon"
    ) as key:
        winreg.SetValue(
            key, "", winreg.REG_SZ, r"%SystemRoot%\System32\imageres.dll,-30"
        )

    # 4. Действие при двойном клике и пункт в контекстном меню ПКМ
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}\shell\open"
    ) as key:
        winreg.SetValue(key, "", winreg.REG_SZ, "Распаковать UnReadableFileSystem")

    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}\shell\open\command"
    ) as key:
        winreg.SetValue(key, "", winreg.REG_SZ, unpack_cmd)

    # Уведомляем Проводник Windows об обновлении ассоциаций (SHChangeNotify)
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except Exception:
        pass

    print(
        "[URFS] Формат '.urfs' успешно зарегистрирован в реестре как 'UnReadableFileSystem'!"
    )
    print(f"       Команда открытия: {unpack_cmd}")


def unregister_from_windows_registry() -> None:
    """Удаляет привязку .urfs из реестра Windows."""
    if os.name != "nt":
        print("[URFS] Ошибка: Доступно только на Windows.")
        sys.exit(1)

    import ctypes
    import winreg

    keys_to_delete = [
        r"Software\Classes\UnReadableFileSystem\shell\open\command",
        r"Software\Classes\UnReadableFileSystem\shell\open",
        r"Software\Classes\UnReadableFileSystem\shell",
        r"Software\Classes\UnReadableFileSystem\DefaultIcon",
        r"Software\Classes\UnReadableFileSystem",
        r"Software\Classes\.urfs",
    ]

    for key_path in keys_to_delete:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except FileNotFoundError:
            pass

    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except Exception:
        pass

    print("[URFS] Регистрация '.urfs' удалена из реестра Windows.")


# -------------------- CLI ИНТЕРФЕЙС --------------------


def main():
    epilog_text = """
ПОДДЕРЖИВАЕМЫЕ ФОРМАТЫ:
  .json                  Дерево JSON-объектов (инод: json)
  .db, .sqlite, .sqlite3 Реляционная база SQLite3: схема + записи (инод: sqlite_db)
  .sql                   SQL-дамп: разворачивается в RAM и сохраняется как БД (инод: sql_dump)
  .csv, .tsv             Табличные данные с разделителями (инод: csv_table)
  *                      Любые прочие файлы упаковываются как бинарный слепок (инод: raw_blob)

РЕЖИМЫ РАЗДЕЛЕНИЯ СТРОК (--mode):
  native  (по умолчанию) Нативные биты (сырые байты) с разделением секторов через \\n (минимальный вес)
  block   1 строка = 1 логический сектор ФС в виде текстовых '0' и '1'
  byte    1 строка = 8 бит (ровно 1 байт на каждой строке из '0' и '1')
  bit     1 строка = 1 бит (каждый символ '1' или '0' на отдельной строке)

ПРИМЕРЫ ЗАПУСКА:
  py urfs.py pack data.json -o data.urfs -z
  py urfs.py pack dump.sql table.csv -o archive.urfs -m block
  py urfs.py unpack data.urfs -d ./unpacked
  py urfs.py register
"""

    parser = argparse.ArgumentParser(
        prog="urfs",
        description="URFS (UnReadableFileSystem) — упаковщик БД и JSON в нативный бинарный или 1/0 формат.",
        epilog=epilog_text,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        dest="command", title="Доступные команды", metavar="<команда>"
    )

    # Команда: pack
    pack_parser = subparsers.add_parser(
        "pack",
        help="Упаковать файлы и БД в образ .urfs",
        description="Упаковка JSON, SQLite, SQL и CSV в единый образ файловой системы .urfs.",
    )
    pack_parser.add_argument(
        "inputs",
        nargs="+",
        metavar="ФАЙЛ",
        help="Один или несколько входных файлов (.json, .db, .sqlite, .sql, .csv, .tsv)",
    )
    pack_parser.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="ПУТЬ.urfs",
        help="Путь к выходному образу .urfs",
    )
    pack_parser.add_argument(
        "-m",
        "--mode",
        choices=["native", "block", "byte", "bit"],
        default="native",
        help="Режим: native (по умолчанию, сырые биты + \\n), block, byte, bit (текстовые 0 и 1)",
    )
    pack_parser.add_argument(
        "-z",
        "--compress",
        action="store_true",
        help="Сжать блоки данных через zlib (уровень 9)",
    )

    # Команда: unpack
    unpack_parser = subparsers.add_parser(
        "unpack",
        help="Распаковать образ .urfs обратно в файлы и базы данных",
        description="Проверка CRC32 и распаковка образа .urfs в исходные файлы и БД.",
    )
    unpack_parser.add_argument(
        "input", metavar="ФАЙЛ.urfs", help="Путь к упакованному образу .urfs"
    )
    unpack_parser.add_argument(
        "-d",
        "--dir",
        default="./urfs_extracted",
        metavar="ПАПКА",
        help="Директория для сохранения извлеченных файлов (по умолчанию: ./urfs_extracted)",
    )

    # Команда: register
    subparsers.add_parser(
        "register",
        help="Зарегистрировать .urfs в реестре Windows как UnReadableFileSystem",
    )

    # Команда: unregister
    subparsers.add_parser(
        "unregister",
        help="Удалить ассоциацию .urfs из реестра Windows",
    )

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)

    args = parser.parse_args()
    compiler = URFSCompiler()

    if args.command == "pack":
        compiler.compile(
            args.inputs, args.output, mode=args.mode, compress=args.compress
        )
    elif args.command == "unpack":
        compiler.decompile(args.input, args.dir)
    elif args.command == "register":
        register_in_windows_registry()
    elif args.command == "unregister":
        unregister_from_windows_registry()


if __name__ == "__main__":
    main()
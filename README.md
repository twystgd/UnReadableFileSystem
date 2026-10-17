# 💽 URFS — UnReadableFileSystem (v2.0)

## Прочитайте README на русском! [Клик](README_RU.md)

![Version](https://img.shields.io/badge/version-2.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.8%2B-brightgreen.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)
![Dependencies](https://img.shields.io/badge/dependencies-zero-orange.svg)
![License](https://img.shields.io/badge/license-MIT-purple.svg)

**URFS (UnReadableFileSystem)** is a compiler, packer, and Virtual File System (VFS) that transforms structured data (**JSON**, **SQLite 3** databases, **SQL dumps**, **CSV/TSV** tables, and raw binaries) into a single, secured `.urfs` image.

Inside a `.urfs` file, data is stored either as an ultra-compact **native bitstream** or as **textual `1` and `0` characters**, where logical sectors, bytes, or individual bits are strictly separated by a newline character (`\n`).

---

## ✨ Key Features

* **📦 Multi-Format VFS Container:** Pack multiple heterogeneous files (`.json`, `.db`, `.sqlite`, `.sql`, `.csv`, `.tsv`) into a single monolithic `.urfs` file while preserving structure and metadata.
* **🗜️ Extreme Compression (up to 21x):** Automatic data structure minification combined with dictionary-based `DEFLATE` compression (zlib level 9) shrinks large JSONs and databases by over **95%**.
* **🎛️ 4 Bit-Representation Modes:**
  * `native` *(default)* — raw hardware bits with sectors separated by `\n` (maximum speed and minimum file size).
  * `block` — textual `1` and `0` characters, where each line represents a distinct VFS sector.
  * `byte` — textual `1` and `0` characters, where each line contains exactly 8 bits (1 byte).
  * `bit` — hardcore mode, where every single bit (`1` or `0`) is written on a new line.
* **🛡️ CRC32 Integrity Control:** The inode directory and every data sector are protected by independent `CRC32` checksums. If a single bit is corrupted, the unpacker will pinpoint the exact location of the error.
* **🪟 Windows Explorer Integration:** Built-in `register` / `unregister` commands add the `.urfs` format to the Windows Registry as a native **`UnReadableFileSystem`** type, apply a custom disk image icon, and enable double-click extraction.
* **⚡ Zero Dependencies:** Written in pure standard Python. No external `pip` libraries required. Can be compiled into a standalone `urfs.exe`.

---

## 📊 Size Comparison: JSON / DB vs `.urfs`

Real-world benchmarks demonstrating the efficiency of the `native` mode with compression (`-z`) compared to raw text formats:

| Test File | Data Description | Original Size | `.urfs` Size (`native -z`) | Difference / Savings |
| :--- | :--- | :--- | :--- | :--- |
| **`example.json`** | Configuration JSON file | `219 B` | **`171 B`** | **-21.9%** *(including VFS headers & CRC32)* |
| **`huge_users.json`** | 5,000 user profiles (135,000 lines) | `2,832 KB` (~2.83 MB) | **`134 KB`** | **Compressed 21.1x (-95.3% of original size)** |

### Comparing URFS Compilation Modes (Based on a 1 KiB file)

| Format / Mode | How Data is Stored in `.urfs` | Human Readability | Final Size (`-z`) |
| :--- | :--- | :--- | :--- |
| **Source `.json`** | UTF-8 text with spaces and line breaks | Human-readable text | `100%` (Baseline) |
| **`.urfs -m native -z`** | Raw bits + `\n` (`0x0A`) sector separators | Unreadable binary gibberish | **`~5% – 35%`** *(3x to 21x smaller)* |
| **`.urfs -m block -z`** | `'0'` and `'1'` characters (1 line = 1 sector) | Only zeros and ones | `~250% – 350%` |
| **`.urfs -m byte -z`** | `'0'` and `'1'` characters (1 line = 8 bits) | 8-bit vertical column | `~280% – 400%` |
| **`.urfs -m bit -z`** | `'0'` and `'1'` characters (1 line = 1 bit) | Vertical tape of 0s and 1s | `~500% – 800%` |

---

## 🧬 `.urfs` Container Architecture (v2)

Every `.urfs` file is a linear virtual file system image separated into sectors by the newline character `\n`:

```text
+-----------------------------------------------------------------------+
| SECTOR 1: SUPERBLOCK (8 bytes / 64 bits)                              |
| [MAGIC: "URFS" (4B)] [VERSION: 2 (1B)] [FLAGS (1B)] [INODES_CNT (2B)] |
+-----------------------------------------------------------------------+
                                   \n
+-----------------------------------------------------------------------+
| SECTOR 2: INODE DIRECTORY                                             |
| [LENGTH (4B)] [CRC32 (4B)] [ZLIB(JSON: [{"i":0,"n":"...","t":"..."}])]|
+-----------------------------------------------------------------------+
                                   \n
+-----------------------------------------------------------------------+
| SECTOR 3..N: DATA BLOCKS                                              |
| [LENGTH (4B)] [CRC32 (4B)] [ZLIB(Serialized DB / Table / JSON)]       |
+-----------------------------------------------------------------------+
                                   \n

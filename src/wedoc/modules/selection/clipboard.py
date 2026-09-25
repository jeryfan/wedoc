"""Clipboard text parsing/stringifying — ports the upstream core clipboard util.

Used by the selection paste family to turn TSV clipboard payloads into a 2D
grid and back. Byte-for-byte compatible with the reference parser (quoted
cells, embedded delimiters/newlines, doubled quotes).
"""

DELIMITER = "\t"
NEWLINE = "\n"
WINDOWS_NEWLINE = "\r\n"


def parse_clipboard_text(content: str) -> list[list[str]]:
    _newline = WINDOWS_NEWLINE if WINDOWS_NEWLINE in content else NEWLINE
    if content.endswith(_newline):
        content = content[: -len(_newline)]
    if '"' not in content:
        return [row.split(DELIMITER) for row in content.split(_newline)]

    length = len(content)
    cursor = 0
    table_data: list[list[str]] = []
    row: list[str] = []
    end_of_row = False
    while cursor < length:
        cell = ""
        quoted = False
        end_of_cell = False
        if content[cursor] == '"':
            quoted = True
        elif content[cursor] == DELIMITER:
            end_of_cell = True
        elif content[cursor] == _newline:
            end_of_cell = True
            end_of_row = True
        else:
            cell += content[cursor]
        while not end_of_cell:
            cursor += 1
            if cursor >= length:
                end_of_cell = True
                end_of_row = True
                cell = f'"{cell}' if quoted else cell
                break
            ch = content[cursor]
            if ch == '"' and quoted:
                if cursor + 1 < length and content[cursor + 1] == '"':
                    cell += '"'
                    cursor += 1
                elif DELIMITER in cell or _newline in cell:
                    quoted = False
                else:
                    cell = f'"{cell}"'
                    quoted = False
            elif ch == DELIMITER:
                if quoted:
                    cell += DELIMITER
                else:
                    end_of_cell = True
                    break
            elif ch == _newline or content[cursor : cursor + len(_newline)] == _newline:
                if quoted:
                    cell += _newline
                else:
                    end_of_cell = True
                    end_of_row = True
                if (
                    content[cursor : cursor + len(_newline)] == _newline
                    and _newline == WINDOWS_NEWLINE
                ):
                    cursor += 1
            else:
                cell += ch
        cursor += 1
        row.append(cell)
        if end_of_cell and cursor >= length and content[cursor - 1] == "\t":
            end_of_row = True
            row.append("")
        if end_of_row:
            table_data.append(row)
            row = []
            end_of_row = False
    return table_data


def stringify_clipboard_text(content: list[list[str]]) -> str:
    out_rows = []
    for row in content:
        cells = []
        for cell in row:
            if DELIMITER in cell or NEWLINE in cell:
                cells.append('"' + cell.replace('"', '""') + '"')
            else:
                cells.append(cell)
        out_rows.append(DELIMITER.join(cells))
    return NEWLINE.join(out_rows)

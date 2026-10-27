import re


def number_to_region(identifier: str) -> str:
    """
    根据给定编号返回其所属的 tRNA 区域。

    区域划分：
      - 氨基酸臂5'端：-1 到 9
      - D 环 + D 臂：10 到 26，以及 17a, 20a, 20b
      - 反密码子环 + 反密码子臂：27 到 43
      - 可变环：44 到 48，以及 V1-5, V11-17, V21-27
      - T 环 + T 臂：49 到 65
      - 氨基酸臂3'端：66 到 76

    支持编号带后缀 iN 的形式，如 17ai1，会先解析前缀再判断。
    如输入无效会抛出 ValueError。
    """
    # 带 iN 后缀的形式，递归去除后缀并保持原 identifier 用于错误报告
    m = re.match(r"^(.+?)i\d+$", identifier)
    if m:
        prefix = m.group(1)
        try:
            return number_to_region(prefix)
        except ValueError:
            # 若前缀依然无效，统一报告原始 identifier
            raise ValueError(f"不支持的编号格式: {identifier}")

    # 纯数字（允许 -1）
    if re.fullmatch(r"-?\d+", identifier):
        num = int(identifier)
        if -1 <= num <= 9:
            return "Aminoacyl arm 5' end"
        if 10 <= num <= 26:
            return "D loop + D stem"
        if 27 <= num <= 43:
            return "Anticodon loop + Anticodon stem"
        if 44 <= num <= 48:
            return "Variable loop"
        if 49 <= num <= 65:
            return "T loop + T stem"
        if 66 <= num <= 76:
            return "Aminoacyl arm 3' end"
        raise ValueError(f"无效的数字编号: {identifier}")

    # 特殊带字母的编号
    if identifier in ("17a", "20a", "20b"):
        return "D loop + D stem"

    # V + 数字 的形式
    if identifier.startswith("V"):
        vnum = identifier[1:]
        if vnum.isdigit():
            n = int(vnum)
            if (1 <= n <= 5) or (11 <= n <= 17) or (21 <= n <= 27):
                return "Variable loop"
        raise ValueError(f"无效的可变环编号: {identifier}")

    # 其他形式均视为不支持
    raise ValueError(f"不支持的编号格式: {identifier}")


if __name__ == "__main__":
    test_ids = [
        "-1", "0", "8", "9", "10", "26", "27", "43", "44", "48", "49", "65", "66", "73", "74",
        "17a", "20a", "20b",
        "V1", "V5", "V17", "V21", "V27", "V28",
        "17ai1", "abc", "abci1"
    ]
    for tid in test_ids:
        try:
            region = number_to_region(tid)
            print(f"{tid}: {region}")
        except ValueError as e:
            print(f"{tid}: Error -> {e}")
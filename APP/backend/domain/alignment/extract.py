import logging


logger = logging.getLogger(__name__)


def _log_extract_error(region: str, sequence: str, structure: str, exc: Exception) -> None:
    logger.warning(
        "Failed to extract %s: %s | sequence=%r | structure=%r",
        region,
        exc,
        sequence,
        structure,
    )

def extract_amino_acid_arm(sequence, structure):
    """
    提取 tRNA 的氨基酸臂 (5′ 端和 3′ 端)：
    返回：(氨基酸臂a碱基序列, 氨基酸臂b碱基序列, 氨基酸臂a结构, 氨基酸臂b结构)
    """
    try:
        # --- 第一步：定位第一个出现的 '<' ---
        pos_first_left = None
        for i, char in enumerate(structure):
            if char == '<':
                pos_first_left = i
                break
        if pos_first_left is None:
            raise ValueError("未在结构中找到 '<'，无法定位 D 环后半段的起点")

        # --- 第二步：从 pos_first_left 开始向后遍历，统计 '<' 数量，直到遇到第一个 '>' ---
        count_left = 0  # 统计遇到的 '<' 数量，即 a
        pos_d_right_boundary = None  # D 臂结束的位置（对应第一个遇到的 '>'）
        i = pos_first_left
        while i < len(structure):
            if structure[i] == '<':
                count_left += 1
            elif structure[i] == '>':
                pos_d_right_boundary = i
                break
            i += 1
        if pos_d_right_boundary is None:
            raise ValueError("在 D 环后半段未遇到 '>'，无法确定 D 臂结束的位置")
        a = count_left  # a 即为统计得到的 '<' 数量

        # --- 第三步：从最初遇到 '<' 的位置向左遍历，累计遇到 a 个 '>' 后得到 D 臂左侧边界 ---
        d_left_boundary = None
        count_gt = 0
        i = pos_first_left - 1
        while i >= 0:
            if structure[i] == '>':
                count_gt += 1
                if count_gt == a:
                    d_left_boundary = i
                    break
            i -= 1
        if d_left_boundary is None:
            raise ValueError("向左遍历时未找到足够的 '>' 匹配 D 环")

        # 氨基酸臂a区域：D 臂左侧边界之前（不含 d_left_boundary）均为氨基酸臂a
        amino_acid_arm_a_seq = sequence[:d_left_boundary]
        amino_acid_arm_a_struct = structure[:d_left_boundary]

        # 统计氨基酸臂a中 '>' 的数量，用作后续匹配
        num_left_stem = structure[:d_left_boundary+1].count('>')

        # --- 第四步：从结构字符串末尾向前查找，寻找第 num_left_stem 个 '<'
        reverse_structure = structure[::-1]
        right_count = 0
        pos_b_start = None  # 氨基酸臂b在结构中的起始位置
        i = 0
        while i < len(reverse_structure):
            if reverse_structure[i] == '<':
                right_count += 1
                # 修改判断条件：应累计到 num_left_stem + 1 个 '<'
                if right_count+1 == num_left_stem:
                    # 向前“吃掉”紧随其后的所有 '.'（占位符）
                    while i + 1 < len(reverse_structure) and reverse_structure[i + 1] == '.':
                        i += 1
                    pos_b_start = len(structure) - i - 1
                    break
            i += 1
        if pos_b_start is None:
            raise ValueError("从结构末尾未找到足够的 '<' 匹配氨基酸臂a")

        amino_acid_arm_b_seq = sequence[pos_b_start:]
        amino_acid_arm_b_struct = structure[pos_b_start:]

        return (
            amino_acid_arm_a_seq,
            amino_acid_arm_b_seq,
            amino_acid_arm_a_struct,
            amino_acid_arm_b_struct
        )
    except Exception as e:
        _log_extract_error("amino acid arm", sequence, structure, e)
        return None
    

    
def extract_d_loop(sequence, structure):
    """
    提取 tRNA 的 D 环 + D臂：
      1. 定位结构中第一个出现的 '<'，认为这是 D 环后半段的起始位置。
      2. 从该位置向后遍历（跳过可能出现的 '.'），统计遇到的 '<' 数量，
         直到遇到第一个 '>'，此处认为是 D 臂结束的位置，记统计出的 '<' 数量为 a。
      3. 从最开始遇到 '<' 的位置向前遍历（同样跳过 '.'），累计遇到 a 个 '>' 后，
         则此时的位置就是 D 臂左侧的边界。
      4. 返回序列中从该左侧边界到 D 臂结束位置之间的子序列（不含结束位置的碱基）。
      5. 同时返回对应的结构子串。
    """
    try:
        # 1. 定位第一个 '<'
        pos_first_left = None
        for i, char in enumerate(structure):
            if char == '<':
                pos_first_left = i
                break
        if pos_first_left is None:
            raise ValueError("未在结构中找到 '<'，无法定位 D 环后半段的起点")

        # 2. 从 pos_first_left 开始向后遍历，统计 '<' 的数量（忽略 '.'），直到遇到下一个 '>'
        count_left = 0
        pos_right_boundary = None
        i = pos_first_left
        while i < len(structure):
            if structure[i] == '<':
                count_left += 1
            elif structure[i] == '>':
                pos_right_boundary = i
                break
            i += 1
        if pos_right_boundary is None:
            raise ValueError("在 D 环后半段未遇到 '>'，无法确定 D 臂结束的位置")

        a = count_left  # 统计到的 '<' 数量

        # 3. 从 pos_first_left 向左遍历，统计 '>' 的数量（忽略 '.'），直到累计遇到 a 个 '>'
        count_right = 0
        pos_left_boundary = None
        i = pos_first_left - 1
        while i >= 0:
            if structure[i] == '>':
                count_right += 1
                if count_right == a:
                    pos_left_boundary = i
                    break
            i -= 1
        if pos_left_boundary is None:
            raise ValueError("向左遍历时未找到足够的 '>' 匹配 D 环")

        # 4. 提取 D 环对应的序列区域（从左边界到右边界，不包括右边界对应的 '>'）
        d_loop_seq = sequence[pos_left_boundary:pos_right_boundary]
        d_loop_struct = structure[pos_left_boundary:pos_right_boundary]

        return (d_loop_seq, d_loop_struct)
    except Exception as e:
        _log_extract_error("D loop", sequence, structure, e)
        return None

def extract_anticodon_arm(sequence, structure):
    """
    提取 tRNA 的反密码子环 + 反密码子臂：
      1. 首先定位 D 环的结束位置（即从结构中第一个出现的 '<' 开始，
         向后遍历直到遇到第一个 '>'，这部分是 D 臂的后半段结束）。
      2. 记录该位置 pos_d_right 作为 anticodon_start，从该处继续向后遍历，
         统计连续出现的 '>'（忽略 '.'），直到遇到第一个 '<'，记数量为 count_gt。
      3. 从遇到 '<' 的位置开始，继续向后遍历，累计遇到 count_gt 个 '<'（同样跳过 '.'），
         即可确定反密码子臂的结束位置 anticodon_end。
      4. 返回从 anticodon_start 到 anticodon_end 之间的序列和对应结构片段。
    """
    try:
        # --- 第一步：定位 D 环结束位置 ---
        pos_first_left = None
        for i, char in enumerate(structure):
            if char == '<':
                pos_first_left = i
                break
        if pos_first_left is None:
            raise ValueError("未在结构中找到 '<'，无法定位 D 环的起点")

        pos_d_right = None
        count_left = 0
        i = pos_first_left
        while i < len(structure):
            if structure[i] == '<':
                count_left += 1
            elif structure[i] == '>':
                pos_d_right = i
                break
            i += 1
        if pos_d_right is None:
            raise ValueError("在 D 环后未遇到 '>'，无法确定 D 环结束位置")

        anticodon_start = pos_d_right

        # --- 第二步：从 anticodon_start 向后遍历，统计连续的 '>' ---
        count_gt = 0
        i = anticodon_start
        while i < len(structure):
            if structure[i] == '>':
                count_gt += 1
            elif structure[i] == '<':
                # 遇到第一个 '<'则停止统计
                break
            i += 1
        if count_gt == 0:
            raise ValueError("在反密码子臂区域未检测到 '>'")
        anticodon_mid = i  # 第一个遇到的 '<'

        # --- 第三步：从 anticodon_mid 开始，向后遍历，累计 a (= count_gt) 个 '<' ---
        count_left = 0
        anticodon_end = anticodon_mid
        while anticodon_end < len(structure) and count_left < count_gt:
            if structure[anticodon_end] == '<':
                count_left += 1
            anticodon_end += 1
        if count_left < count_gt:
            raise ValueError("未能找到足够的 '<' 来匹配反密码子臂")

        # --- 第四步：提取反密码子臂对应的序列和结构 ---
        anticodon_arm_seq = sequence[anticodon_start:anticodon_end]
        anticodon_arm_struct = structure[anticodon_start:anticodon_end]

        return (anticodon_arm_seq, anticodon_arm_struct)
    except Exception as e:
        _log_extract_error("anticodon arm", sequence, structure, e)
        return None

def extract_variable_loop(sequence, structure):
    """
    提取 tRNA 的可变环：
      1. 定位反密码子臂结束位置（anticodon_end）：
         a. 从结构中第一个出现的 '<' 开始，向后遍历直到遇到第一个 '>'，得 D 环结束位置 anticodon_start。
         b. 从 anticodon_start 开始向后遍历，统计连续出现的 '>'（忽略 '.'），
            记数量为 count_gt；遇到第一个 '<' 后，再向后遍历累计遇到 count_gt 个 '<'，
            得 anticodon_end。
      2. 确定 T 臂起始边界（pos_t_left）：
         a. 调用 extract_amino_acid_arm 得到氨基酸臂b部分，从对应结构子串中统计 '<' 数量，记为 num_b。
         b. 在结构中找到最后一次出现的 '>'（pos_last_gt），从该位置向右扫描，
            统计所有 '<' 的数量 total_lt_right。
         c. 计算 T 臂后半段应包含的 '<' 数量： t_arm_lt = total_lt_right - num_b。
         d. 从 pos_last_gt 向左扫描，累计遇到 t_arm_lt 个 '>'，得 pos_t_left。
      3. 可变环区域为 sequence[anticodon_end : pos_t_left]（以及对应结构部分）。
    """
    try:
        # --- 步骤1：确定反密码子臂结束位置 anticodon_end ---
        # 1a. 定位第一个 '<'
        pos_first_left = None
        for i, char in enumerate(structure):
            if char == '<':
                pos_first_left = i
                break
        if pos_first_left is None:
            raise ValueError("未找到 '<'，无法定位 D 环起点")

        # 从 pos_first_left 向后遍历，直到遇到第一个 '>'，确定 D 环结束位置（anticodon_start）
        pos_d_right = None
        count_left = 0
        i = pos_first_left
        while i < len(structure):
            if structure[i] == '<':
                count_left += 1
            elif structure[i] == '>':
                pos_d_right = i
                break
            i += 1
        if pos_d_right is None:
            raise ValueError("未在 D 环后找到 '>'，无法确定 D 环结束位置")
        anticodon_start = pos_d_right

        # 从 anticodon_start 向后遍历，统计连续出现的 '>'（忽略 '.'）
        count_gt = 0
        i = anticodon_start
        while i < len(structure):
            if structure[i] == '>':
                count_gt += 1
            elif structure[i] == '<':
                break
            i += 1
        if count_gt == 0:
            raise ValueError("在反密码子臂区域未检测到 '>'")
        anticodon_mid = i  # 第一个遇到的 '<'

        # 从 anticodon_mid 向后遍历，累计遇到 count_gt 个 '<'
        count_lt = 0
        anticodon_end = anticodon_mid
        while anticodon_end < len(structure) and count_lt < count_gt:
            if structure[anticodon_end] == '<':
                count_lt += 1
            anticodon_end += 1
        if count_lt < count_gt:
            raise ValueError("未能找到足够的 '<' 来匹配反密码子臂")

        # --- 步骤2：确定 T 臂起始边界 pos_t_left ---
        aa_arms = extract_amino_acid_arm(sequence, structure)
        if aa_arms is None:
            raise ValueError("氨基酸臂提取失败，无法定位 T 臂")
        # aa_arms 返回 (amino_acid_arm_a_seq, amino_acid_arm_b_seq, amino_acid_arm_a_struct, amino_acid_arm_b_struct)
        _, amino_acid_arm_b_seq, _, amino_acid_arm_b_struct = aa_arms

        num_b = amino_acid_arm_b_struct.count('<')

        # 定位结构中最后一次出现的 '>'
        pos_last_gt = structure.rfind('>')
        if pos_last_gt == -1:
            raise ValueError("未在结构中找到 '>'，无法定位 T 臂终点")

        right_sub = structure[pos_last_gt+1:]
        total_lt_right = right_sub.count('<')

        t_arm_lt = total_lt_right - num_b
        if t_arm_lt < 0:
            raise ValueError("计算得到 T 臂后半段 '<' 数量为负")
        if t_arm_lt == 0:
            # 若没有额外的 '<'，则可变环为空
            return ("", "")

        # 从 pos_last_gt 向左遍历，累计遇到 t_arm_lt 个 '>'
        count_gt_t = 0
        pos_t_left = None
        i = pos_last_gt
        while i >= 0:
            if structure[i] == '>':
                count_gt_t += 1
                if count_gt_t == t_arm_lt:
                    pos_t_left = i
                    break
            i -= 1
        if pos_t_left is None:
            raise ValueError("未能在 T 臂前半段找到足够的 '>'")

        # --- 步骤3：提取可变环序列 ---
        if anticodon_end > pos_t_left:
            # 若反密码子臂结束位置超过了 T 臂起始，则认为可变环为空
            return ("", "")
        variable_loop_seq = sequence[anticodon_end: pos_t_left]
        variable_loop_struct = structure[anticodon_end: pos_t_left]

        return (variable_loop_seq, variable_loop_struct)
    except Exception as e:
        _log_extract_error("variable loop", sequence, structure, e)
        return None

def extract_t_arm(sequence, structure):
    """
    提取 tRNA 的 T 环 + T 臂：
      1. 先调用 extract_amino_acid_arm 得到氨基酸臂b部分，从对应结构子串中统计 '<' 的数量，记为 num_b。
      2. 在结构中找到最后一次出现的 '>'（pos_last_gt），从其右侧统计所有 '<' 的数量 total_lt_right。
      3. 计算 T 臂后半段应包含的 '<' 数量： t_arm_lt = total_lt_right - num_b。
      4. 从 pos_last_gt 往左扫描，统计 '>' 直到累计达到 t_arm_lt，得到 T 臂左边界 pos_t_left。
      5. 从 pos_last_gt 往右扫描，累计 t_arm_lt 个 '<'，得到 T 臂右边界 pos_t_right。
      6. 返回序列中对应区域 sequence[pos_t_left: pos_t_right+1] 以及对应的结构。
    """
    try:
        # --- 步骤1：获取氨基酸臂b，并统计其中 '<' 的数量 ---
        aa_arms = extract_amino_acid_arm(sequence, structure)
        if aa_arms is None:
            raise ValueError("氨基酸臂提取失败，无法确定 T 臂边界")
        # aa_arms 返回 (amino_acid_arm_a_seq, amino_acid_arm_b_seq, amino_acid_arm_a_struct, amino_acid_arm_b_struct)
        _, amino_acid_arm_b_seq, _, amino_acid_arm_b_struct = aa_arms
        num_b = amino_acid_arm_b_struct.count('<')

        # --- 步骤2：定位结构中最后一次出现的 '>' ---
        pos_last_gt = structure.rfind('>')
        if pos_last_gt == -1:
            raise ValueError("结构中未找到 '>'，无法定位 T 臂终点")

        # --- 步骤3：从 pos_last_gt 往右扫描，统计所有 '<' 的数量 ---
        right_sub = structure[pos_last_gt+1:]
        total_lt_right = right_sub.count('<')

        # --- 步骤4：计算 T 臂后半段应包含的 '<' 数量 ---
        t_arm_lt = total_lt_right - num_b
        if t_arm_lt < 0:
            raise ValueError("计算得到 T 臂后半段 '<' 数量为负，可能氨基酸臂b结构错误")
        if t_arm_lt == 0:
            # 如果没有额外的 '<'，则认为 T 臂不存在
            return ("", "")

        # --- 步骤5：从 pos_last_gt 往左扫描，统计 '>' 直到累计达到 t_arm_lt，得到 T 臂左边界 ---
        count_gt = 0
        pos_t_left = None
        i = pos_last_gt
        while i >= 0:
            if structure[i] == '>':
                count_gt += 1
                if count_gt == t_arm_lt:
                    pos_t_left = i
                    break
            i -= 1
        if pos_t_left is None:
            raise ValueError("未能在 T 臂前半段找到足够的 '>'")

        # --- 步骤6：从 pos_last_gt 往右扫描，累计 t_arm_lt 个 '<'，得到 T 臂右边界 ---
        count_lt = 0
        pos_t_right = None
        i = pos_last_gt + 1
        while i < len(structure):
            if structure[i] == '<':
                count_lt += 1
                if count_lt == t_arm_lt:
                    pos_t_right = i
                    break
            i += 1
        if pos_t_right is None:
            raise ValueError("未能在 T 臂后半段找到足够的 '<'")

        # --- 步骤7：提取 T 臂对应的序列和结构 ---
        t_arm_seq = sequence[pos_t_left: pos_t_right+1]
        t_arm_struct = structure[pos_t_left: pos_t_right+1]

        return (t_arm_seq, t_arm_struct)
    except Exception as e:
        _log_extract_error("T arm", sequence, structure, e)
        return None

if __name__ == "__main__":
    sequence = "GGAGGUGUAGCGGAAUUGGUAAACGCCAGUCUCAAUCUGUUUGCAGGUUCAAGUCCUGCCACCUCCC"
    structure = ">>>>>>>..>>>...........<<<................>>>>>.......<<<<<<<<<<<<."

    # 1. 提取氨基酸臂 (5′ 端) a 和 (3′ 端) b
    aa_arms = extract_amino_acid_arm(sequence, structure)
    if aa_arms:
        aa_arm_a_seq, aa_arm_b_seq, aa_arm_a_struct, aa_arm_b_struct = aa_arms
        print("=== 氨基酸臂 (Acceptor Arm) ===")
        print(f"氨基酸臂a (5′端) 碱基序列: {aa_arm_a_seq}")
        print(f"氨基酸臂a (5′端) 结构子串: {aa_arm_a_struct}")
        print(f"氨基酸臂b (3′端) 碱基序列: {aa_arm_b_seq}")
        print(f"氨基酸臂b (3′端) 结构子串: {aa_arm_b_struct}")
    else:
        print("氨基酸臂提取失败。")

    # 2. 提取 D 环 + D 臂
    d_loop = extract_d_loop(sequence, structure)
    if d_loop:
        d_loop_seq, d_loop_struct = d_loop
        print("\n=== D 环 + D 臂 ===")
        print(f"D 环+D臂序列: {d_loop_seq}")
        print(f"D 环+D臂结构: {d_loop_struct}")
    else:
        print("D 环+D臂提取失败。")

    # 3. 提取反密码子臂
    anticodon_arm = extract_anticodon_arm(sequence, structure)
    if anticodon_arm:
        anticodon_arm_seq, anticodon_arm_struct = anticodon_arm
        print("\n=== 反密码子环 + 反密码子臂 ===")
        print(f"反密码子臂序列: {anticodon_arm_seq}")
        print(f"反密码子臂结构: {anticodon_arm_struct}")
    else:
        print("反密码子臂提取失败。")

    # 4. 提取可变环
    variable_loop = extract_variable_loop(sequence, structure)
    if variable_loop:
        var_loop_seq, var_loop_struct = variable_loop
        print("\n=== 可变环 (Variable Loop) ===")
        print(f"可变环序列: {var_loop_seq}")
        print(f"可变环结构: {var_loop_struct}")
    else:
        print("可变环可能不存在或提取失败。")

    # 5. 提取 T 环 + T 臂
    t_arm = extract_t_arm(sequence, structure)
    if t_arm:
        t_arm_seq, t_arm_struct = t_arm
        print("\n=== T 环 + T 臂 ===")
        print(f"T 环+T臂序列: {t_arm_seq}")
        print(f"T 环+T臂结构: {t_arm_struct}")
    else:
        print("T 环+T 臂提取失败。")

"""中文→英文学科词表与确定性翻译层（add-multilingual-zh-search D1/D4）。

口径（数据契约，纯数据零逻辑分支——扩覆盖=加词条，不改代码）：
- 词表覆盖教育域学科/课程/常见主题的 zh→en 对照；英文与目录 slug 双侧归一连字符后
  做子串匹配，因此英文值用自然空格词组（如 "linear algebra"）。
- translate_query() 最长前匹配分词；词表未覆盖的 CJK 片段**整体丢弃**（不编造），
  非 CJK 片段原样保留；全部 CJK 未覆盖或无 CJK 时返回 None（不翻译，按原样查）。
- 翻译对照 MUST 随响应可见（tools.edu_search 的 query_translated），命中与否都带。
"""

CJK_RANGES = (
    (0x4E00, 0x9FFF),   # CJK 统一表意文字
    (0x3400, 0x4DBF),   # 扩展 A
)

# zh → en（教育域；英文值用小写自然词组，供 hay 归一后子串匹配）
ZH_EN_TERMS: dict[str, str] = {
    # ── 学科大类 ──
    "数学": "mathematics", "物理": "physics", "化学": "chemistry", "生物": "biology",
    "计算机": "computer science", "计算机科学": "computer science", "经济": "economics",
    "金融": "finance", "法律": "law", "法学": "law", "社会学": "sociology",
    "心理学": "psychology", "哲学": "philosophy", "历史": "history", "文学": "literature",
    "语言学": "linguistics", "教育": "education", "教育学": "education", "工程": "engineering",
    "统计": "statistics", "统计学": "statistics", "艺术": "art", "音乐": "music",
    "医学": "medicine", "管理": "management", "会计": "accounting", "营销": "marketing",
    "人类学": "anthropology", "政治": "political science", "政治学": "political science",
    "地理": "geography", "天文": "astronomy", "天文学": "astronomy",
    "环境": "environmental", "建筑": "architecture", "建筑学": "architecture",
    "考古": "archaeology", "考古学": "archaeology", "新闻": "journalism",
    # ── 数学课程 ──
    "微积分": "calculus", "线性代数": "linear algebra", "代数": "algebra",
    "几何": "geometry", "概率": "probability", "概率论": "probability",
    "离散数学": "discrete mathematics", "数论": "number theory", "拓扑": "topology",
    "优化": "optimization", "常微分方程": "differential equations",
    "偏微分方程": "partial differential equations", "方程": "equations",
    "抽象代数": "abstract algebra", "实分析": "real analysis", "复分析": "complex analysis",
    "泛函分析": "functional analysis", "数学分析": "mathematical analysis",
    # ── 计算机课程 ──
    "算法": "algorithms", "数据结构": "data structures", "机器学习": "machine learning",
    "深度学习": "deep learning", "人工智能": "artificial intelligence",
    "自然语言处理": "natural language processing", "数据库": "databases",
    "计算机网络": "computer networks", "操作系统": "operating systems",
    "编译": "compiler", "编译器": "compiler", "密码学": "cryptography",
    "软件工程": "software engineering", "编程": "programming", "程序设计": "programming",
    "神经网络": "neural networks", "强化学习": "reinforcement learning",
    "计算机视觉": "computer vision", "大数据": "big data", "数据科学": "data science",
    "网页开发": "web development", "网络安全": "cybersecurity",
    # ── 物理课程 ──
    "力学": "mechanics", "电磁": "electromagnetism", "电磁学": "electromagnetism",
    "热力学": "thermodynamics", "量子力学": "quantum mechanics", "量子": "quantum",
    "相对论": "relativity", "光学": "optics", "天体物理": "astrophysics",
    "粒子物理": "particle physics", "统计物理": "statistical physics",
    # ── 化学/生物/医学 ──
    "有机化学": "organic chemistry", "无机化学": "inorganic chemistry",
    "分析化学": "analytical chemistry", "物理化学": "physical chemistry",
    "生物化学": "biochemistry", "遗传": "genetics", "遗传学": "genetics",
    "分子生物": "molecular biology", "分子生物学": "molecular biology",
    "生态": "ecology", "生态学": "ecology", "进化": "evolution",
    "神经科学": "neuroscience", "微生物": "microbiology", "微生物学": "microbiology",
    "基因": "gene", "细胞": "cell biology", "细胞生物学": "cell biology",
    "解剖": "anatomy", "解剖学": "anatomy", "生理": "physiology", "生理学": "physiology",
    "药理": "pharmacology", "药理学": "pharmacology", "流行病": "epidemiology",
    "流行病学": "epidemiology", "公共卫生": "public health", "营养": "nutrition",
    "免疫": "immunology", "免疫学": "immunology",
    # ── 经济/金融/管理 ──
    "微观经济": "microeconomics", "微观经济学": "microeconomics",
    "宏观经济": "macroeconomics", "宏观经济学": "macroeconomics",
    "计量经济": "econometrics", "计量经济学": "econometrics",
    "博弈论": "game theory", "国际贸易": "international trade",
    "发展经济": "development economics", "发展经济学": "development economics",
    "货币政策": "monetary policy", "公司金融": "corporate finance",
    "投资": "investment", "创业": "entrepreneurship", "供应链": "supply chain",
    "市场": "marketing",
    # ── 工程/应用 ──
    "机器人": "robotics", "机器人学": "robotics", "信号": "signals",
    "信号处理": "signal processing", "控制": "control systems",
    "控制理论": "control systems", "材料科学": "materials science",
    "航空": "aerodynamics", "空气动力学": "aerodynamics", "航天": "aerospace",
    "电子": "electronics", "电路": "circuits", "电路设计": "circuit design",
    "机械": "mechanical engineering", "土木": "civil engineering",
    "电气": "electrical engineering", "化学工程": "chemical engineering",
    "能源": "energy", "可持续": "sustainability", "气候变化": "climate change",
    "城市规划": "urban planning", "结构": "structural engineering",
    # ── 人文社科 ──
    "世界历史": "world history", "中国历史": "chinese history",
    "艺术史": "art history", "伦理": "ethics", "伦理学": "ethics",
    "逻辑": "logic", "逻辑学": "logic", "形而上学": "metaphysics",
    "认识论": "epistemology", "国际关系": "international relations",
    "宪法": "constitutional law", "刑法": "criminal law", "民法": "civil law",
    "国际法": "international law", "写作": "writing", "学术写作": "academic writing",
    "演讲": "public speaking", "英语": "english", "翻译": "translation",
    "诗歌": "poetry", "戏剧": "drama", "小说": "fiction",
    # ── 教育/学习 ──
    "课程设计": "curriculum", "教学法": "pedagogy", "评估": "assessment",
    "课堂": "classroom", "学习": "learning", "教学": "teaching",
    "远程教育": "distance education", "在线学习": "online learning",
}


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in CJK_RANGES)


def has_cjk(text: str) -> bool:
    return any(_is_cjk(ch) for ch in text or "")


def translate_query(query: str) -> str | None:
    """zh→en 确定性翻译：最长前匹配分词；未覆盖 CJK 丢弃；全未覆盖/无 CJK → None。

    返回小写英文字符串（词组以空格连接，与归一后的 hay 匹配）。
    """
    q = (query or "").strip()
    if not q or not has_cjk(q):
        return None
    out: list[str] = []
    i = 0
    matched_any = False
    while i < len(q):
        ch = q[i]
        if not _is_cjk(ch):
            # 非 CJK 片段原样保留（含英文/数字/空格）
            j = i
            while j < len(q) and not _is_cjk(q[j]):
                j += 1
            out.append(q[i:j])
            i = j
            continue
        # CJK 起点向后最长前匹配词表
        best_end = None
        for end in range(len(q), i, -1):
            if q[i:end] in ZH_EN_TERMS:
                best_end = end
                break
        if best_end:
            out.append(ZH_EN_TERMS[q[i:best_end]])
            matched_any = True
            i = best_end
        else:
            # 词表未覆盖的 CJK 字符：丢弃（不编造），跳一个字
            i += 1
    if not matched_any:
        return None
    return " ".join(tok for tok in " ".join(out).split() if tok)

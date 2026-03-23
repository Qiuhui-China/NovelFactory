"""
pipeline.py — 番茄短故事工厂 v2
用法：
  python pipeline.py                    # 新建一篇
  python pipeline.py --resume           # 从断点继续
  python pipeline.py --book-name NAME   # 指定项目名
"""
import os, sys, json, yaml, argparse, re
from pathlib import Path
from datetime import datetime
from zhipu_client import ZhipuClient


# ── 工具函数 ──────────────────────────────────

def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_prompt(name, **kwargs):
    path = Path("prompts") / name
    with open(path, "r", encoding="utf-8") as f:
        t = f.read()
    for k, v in kwargs.items():
        ph = "{" + k + "}"
        if ph in t:
            t = t.replace(ph, str(v))
    return t

def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def save_json(data, fp):
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_json(fp):
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)

def save_text(text, fp):
    with open(fp, "w", encoding="utf-8") as f:
        f.write(text)

def load_text(fp):
    with open(fp, "r", encoding="utf-8") as f:
        return f.read()

def fix_json_string(s):
    """修复智谱常见的JSON格式问题。"""
    # 1. 如果整个字符串被包在引号里（双重转义），先解包
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        try:
            inner = json.loads(s)  # 解掉外层引号
            if isinstance(inner, str):
                s = inner
        except:
            pass
    
    # 2. 修复混用单双引号：把JSON值中的单引号替换为双引号
    #    策略：找到 : '...' 模式，替换为 : "..."
    #    注意不要替换中文内容里的单引号
    def replace_single_quotes(text):
        """替换JSON结构中的单引号为双引号，保留中文内容中的单引号。"""
        result = []
        i = 0
        while i < len(text):
            # 找 key: 'value' 模式（冒号后面跟单引号）
            if text[i] == "'" and i > 0:
                # 往回找，看前面是不是冒号（跳过空白）
                j = i - 1
                while j >= 0 and text[j] in ' \t\n\r':
                    j -= 1
                if j >= 0 and text[j] in ':,[\n':
                    # 找配对的结尾单引号：必须后面紧跟 JSON 结构符（,  \n  }  ]  空白）
                    # 这样可以跳过值内部的 '词语' 引用，找到真正的字符串边界
                    end = i + 1
                    found = False
                    while end < len(text):
                        if text[end] == "'" and text[end - 1] != '\\':
                            next_ch = text[end + 1] if end + 1 < len(text) else ''
                            if next_ch in ',\n}] \t\r' or next_ch == '':
                                found = True
                                break
                        end += 1
                    if found:
                        # 替换这对单引号为双引号，内部的双引号转义
                        inner = text[i+1:end].replace('"', '\\"')
                        result.append('"')
                        result.append(inner)
                        result.append('"')
                        i = end + 1
                        continue
            result.append(text[i])
            i += 1
        return ''.join(result)
    
    s = replace_single_quotes(s)

    # 3. 修复值内部的未转义双引号：找到 ": "..." 模式，把内部多余的 " 转义
    #    策略：扫描到冒号后的开头引号，逐字符读取，遇到 " 时判断后面是否是 JSON 结构符；
    #    不是结构符则认定为内部引号，转义为 \"
    def escape_inner_double_quotes(text):
        result = []
        i = 0
        n = len(text)
        while i < n:
            # 检测值开头引号：当前是 " 且前面紧跟冒号（跳过空白）
            if text[i] == '"':
                j = i - 1
                while j >= 0 and text[j] in ' \t':
                    j -= 1
                if j >= 0 and text[j] == ':':
                    # 进入字符串值扫描模式
                    result.append('"')
                    i += 1
                    while i < n:
                        if text[i] == '\\' and i + 1 < n:
                            # 已转义，原样保留
                            result.append(text[i])
                            result.append(text[i + 1])
                            i += 2
                        elif text[i] == '"':
                            # 判断是真正结尾还是内部引号
                            k = i + 1
                            while k < n and text[k] in ' \t':
                                k += 1
                            if k >= n or text[k] in ',\n}]':
                                result.append('"')
                                i += 1
                                break
                            else:
                                result.append('\\"')
                                i += 1
                        else:
                            result.append(text[i])
                            i += 1
                    continue
            result.append(text[i])
            i += 1
        return ''.join(result)

    s = escape_inner_double_quotes(s)
    return s

def extract_json(text):
    """从AI回复中提取JSON。处理智谱常见的双重转义和混用引号问题。"""
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        text = m.group(1)
    text = text.strip()
    
    def try_all_strategies(s):
        """对一个字符串尝试所有解析策略。"""
        # 策略1：直接解析
        try:
            return json.loads(s)
        except (json.JSONDecodeError, TypeError):
            pass
        # 策略2：修复引号后解析
        try:
            return json.loads(fix_json_string(s))
        except (json.JSONDecodeError, TypeError):
            pass
        # 策略3：提取 {} 或 [] 区间
        for sc, ec in [('{', '}'), ('[', ']')]:
            start, end = s.find(sc), s.rfind(ec)
            if start != -1 and end != -1:
                chunk = s[start:end+1]
                try:
                    return json.loads(chunk)
                except:
                    pass
                try:
                    return json.loads(fix_json_string(chunk))
                except:
                    pass
        return None
    
    # 第一轮：直接尝试
    result = try_all_strategies(text)
    if result is not None and not isinstance(result, str):
        return result
    
    # 第二轮：如果第一轮得到的是字符串（双重转义），对内部字符串再跑一遍
    if isinstance(result, str):
        inner = try_all_strategies(result)
        if inner is not None:
            return inner
    
    # 第三轮：ast.literal_eval 兜底（能处理Python风格的单引号dict）
    try:
        import ast
        return ast.literal_eval(text)
    except:
        pass

    # 第四轮：json_repair 库（专门处理各种畸形JSON）
    try:
        from json_repair import repair_json
        repaired = repair_json(text, return_objects=True)
        if repaired is not None and not isinstance(repaired, str):
            return repaired
        # 双重编码情况：repair_json 可能返回字符串
        if isinstance(repaired, str):
            inner = repair_json(repaired, return_objects=True)
            if inner is not None and not isinstance(inner, str):
                return inner
    except ImportError:
        pass
    except Exception:
        pass

    print("  ⚠ JSON解析失败，返回原始文本")
    return result if result is not None else text

def human_pause(stage, filepath):
    print(f"\n{'='*60}")
    print(f"🔍 【人工审核】{stage}")
    print(f"   文件: {filepath}")
    print(f"{'='*60}\n")
    while True:
        c = input("[c]确认继续 [e]已编辑用新版 [r]重新生成 [q]保存退出\n>>> ").strip().lower()
        if c in ('c','e','r','q'):
            return c
        print("请输入 c/e/r/q")


# ── 进度管理 ──────────────────────────────────

class Progress:
    def __init__(self, book_dir):
        self.fp = os.path.join(book_dir, "progress.json")
        self.data = {"stage": "style_card", "started": datetime.now().isoformat()}
        if os.path.exists(self.fp):
            self.data = load_json(self.fp)
    
    def save(self):
        self.data["updated"] = datetime.now().isoformat()
        save_json(self.data, self.fp)
    
    def get(self):
        return self.data.get("stage", "style_card")
    
    def set(self, stage):
        self.data["stage"] = stage
        self.save()


# ── 已用风格日志 ──────────────────────────────

def load_style_history(output_dir):
    fp = os.path.join(output_dir, "style_history.json")
    if os.path.exists(fp):
        return load_json(fp)
    return []

def save_style_history(output_dir, history):
    save_json(history, os.path.join(output_dir, "style_history.json"))


# ── 试读者评估 + 自动修改 ─────────────────────

def run_reader_eval(client, config, content, eval_type, book_dir, label):
    """试读者评估，返回评估结果。"""
    prompt = load_prompt("eval_reader.md", eval_type=eval_type, content=content)
    resp = client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=config["models"]["creative"],
        temperature=0.5, max_tokens=2000,
        stage="reader_eval", label=label,
        json_mode=True,
    )
    result = extract_json(resp)
    save_json(result, os.path.join(book_dir, f"eval_{label}.json"))
    save_text(resp, os.path.join(book_dir, f"eval_{label}_raw.md"))
    
    # 提取总分
    score = 7  # 默认
    if isinstance(result, dict):
        overall = result.get("overall", {})
        if isinstance(overall, dict):
            score = overall.get("score", 7)
    
    print(f"  📖 试读者评分: {score}/10")
    return result, score

def auto_fix(client, config, content, eval_type, feedback, book_dir, label):
    """根据试读者反馈自动修改。"""
    prompt = load_prompt(
        "fix_based_on_feedback.md",
        eval_type=eval_type,
        reader_feedback_json=json.dumps(feedback, ensure_ascii=False, indent=2),
        content=content,
    )
    resp = client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=config["models"]["creative"],
        temperature=config["generation"]["temperature_writing"],
        max_tokens=config["generation"]["max_tokens_long"],
        stage="auto_fix", label=label,
    )
    save_text(resp, os.path.join(book_dir, f"fix_{label}.md"))
    return resp

def eval_and_fix_loop(client, config, content, eval_type, book_dir, label,
                      regenerate_fn=None):
    """评估→至少1轮优化→达标停止。
    regenerate_fn: 可选回调 fn(extra_instructions=str) -> new_content
                   提供时用重新生成代替 auto_fix（适用于JSON类型内容）。
    """
    threshold = config["resilience"]["quality_threshold"]
    max_rev = config["resilience"]["max_revisions"]

    for i in range(max_rev + 1):
        feedback, score = run_reader_eval(
            client, config, content, eval_type, book_dir, f"{label}_v{i}"
        )
        # 已经做过至少1轮优化后才允许提前退出
        if score >= threshold and i >= 1:
            print(f"  ✅ 质量达标 ({score}>={threshold})")
            return content, feedback

        if i < max_rev:
            if score >= threshold:
                print(f"  ✅ 首次评分达标 ({score}>={threshold})，仍进行1轮优化以提升质量...")
            else:
                print(f"  🔧 质量未达标 ({score}<{threshold})，自动修改第{i+1}次...")

            # 提取 must_fix 建议
            must_fix = ""
            if isinstance(feedback, dict):
                fixes = feedback.get("must_fix", [])
                if isinstance(fixes, list):
                    must_fix = "\n".join(f"- {f}" for f in fixes)

            if regenerate_fn:
                # JSON内容：带改进建议重新生成，避免修改时破坏结构
                # must_fix 为空时也重新生成（不传额外指令），绝不对JSON字符串调用auto_fix
                content = regenerate_fn(extra_instructions=must_fix)
            else:
                # 文本内容：走原来的 auto_fix
                content = auto_fix(client, config, content, eval_type,
                                   feedback, book_dir, f"{label}_fix{i}")

    print(f"  ⚠ 已达最大修改次数，使用当前版本")
    return content, feedback


# ── 各阶段实现 ────────────────────────────────

def stage_style_card(client, config, book_dir, output_dir):
    """阶段0：主编生成风格卡。"""
    print("\n🎨 阶段0：生成风格卡...")
    
    pool = load_yaml("style_pool.yaml")
    history = load_style_history(output_dir)
    
    # 构建风格池摘要
    pool_summary = ""
    for track in pool["tracks"]:
        pool_summary += f"\n### 赛道：{track['name']}\n{track['desc']}\n变体：\n"
        for v in track["variants"]:
            pool_summary += f"  - {v}\n"
    pool_summary += "\n### 女主性格\n"
    for v in pool["protagonist_voices"]:
        pool_summary += f"  - {v['id']}：{v['desc']}（示例：{v['sample']}）\n"
    pool_summary += "\n### 幽默风格\n"
    for h in pool["humor_styles"]:
        pool_summary += f"  - {h['id']}：{h['desc']}\n"
    pool_summary += "\n### 打脸方式\n"
    for f in pool["face_slap_styles"]:
        pool_summary += f"  - {f}\n"
    pool_summary += "\n### 感情线风味\n"
    for r in pool["romance_flavors"]:
        pool_summary += f"  - {r}\n"
    
    # 已用历史
    hist_text = "无" if not history else json.dumps(history, ensure_ascii=False, indent=2)
    
    prompt = load_prompt("00_style_card.md",
                         style_pool_summary=pool_summary, used_history=hist_text)
    
    resp = client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=config["models"]["creative"],
        temperature=config["generation"]["temperature_creative"],
        max_tokens=config["generation"]["max_tokens_short"],
        stage="style_card", label="gen",
        json_mode=True,
    )
    
    card = extract_json(resp)
    save_json(card, os.path.join(book_dir, "00_style_card.json"))
    save_text(resp, os.path.join(book_dir, "00_style_card_raw.md"))
    
    # 打印摘要
    if isinstance(card, dict):
        print(f"  赛道: {card.get('track','?')} | 性格: {card.get('protagonist_voice','?')}")
        print(f"  气质: {card.get('tone_summary','?')}")
    
    return card

def _unwrap_topic_list(data):
    """json_object模式可能把顶层数组包成 {"topics":[...]} 或 {"list":[...]}，
    此函数把它还原成列表。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v:
                return v
    return data

def stage_topic_debate(client, config, book_dir, style_card):
    """阶段1：双作者对喷选题。"""
    print("\n💡 阶段1：选题对喷...")
    
    track = style_card.get("track", "女配觉醒") if isinstance(style_card, dict) else "女配觉醒"
    card_json = json.dumps(style_card, ensure_ascii=False, indent=2)
    
    # 1a: 作者A出方案
    print("  → 作者A出3个方案...")
    prompt_a = load_prompt("01a_topic_author.md", track=track, style_card_json=card_json)
    resp_a = client.chat(
        messages=[{"role": "user", "content": prompt_a}],
        model=config["models"]["creative"],
        temperature=config["generation"]["temperature_creative"],
        max_tokens=config["generation"]["max_tokens_medium"],
        stage="topic", label="author_a",
        json_mode=True,
    )
    topics = extract_json(resp_a)
    topics = _unwrap_topic_list(topics)
    save_json(topics, os.path.join(book_dir, "01a_topics.json"))

    # 1b: 编辑B吐槽
    print("  → 编辑B吐槽...")
    topics_json = json.dumps(topics, ensure_ascii=False, indent=2)
    prompt_b = load_prompt("01b_topic_critic.md", topics_json=topics_json)
    resp_b = client.chat(
        messages=[{"role": "user", "content": prompt_b}],
        model=config["models"]["creative"],
        temperature=0.7, max_tokens=config["generation"]["max_tokens_short"],
        stage="topic", label="critic",
        json_mode=True,
    )
    critic = extract_json(resp_b)
    save_json(critic, os.path.join(book_dir, "01b_critic.json"))
    
    # 1c: 作者A修改
    print("  → 作者A根据吐槽修改...")
    critic_json = json.dumps(critic, ensure_ascii=False, indent=2)
    prompt_c = load_prompt("01c_topic_revise.md",
                           topics_json=topics_json, critic_json=critic_json)
    resp_c = client.chat(
        messages=[{"role": "user", "content": prompt_c}],
        model=config["models"]["creative"],
        temperature=config["generation"]["temperature_creative"],
        max_tokens=config["generation"]["max_tokens_medium"],
        stage="topic", label="revise",
        json_mode=True,
    )
    revised = extract_json(resp_c)
    revised = _unwrap_topic_list(revised)
    save_json(revised, os.path.join(book_dir, "01c_revised.json"))
    save_text(resp_c, os.path.join(book_dir, "01c_revised_raw.md"))
    
    return revised

def pick_topic_interactive(topics, book_dir):
    """让用户选一个方案。"""
    if not isinstance(topics, list) or not topics:
        print("  ⚠ 选题格式异常")
        return topics[0] if isinstance(topics, list) and topics else topics
    
    print("\n请选择方案：")
    for t in topics:
        tid = t.get("id", "?")
        title = t.get("title", "?")
        hook = t.get("hook_opening", "")[:60]
        print(f"  [{tid}] {title}")
        print(f"      {hook}...")
    
    while True:
        pick = input(f"\n输入方案编号 (1-{len(topics)}): ").strip()
        try:
            selected = next(t for t in topics if str(t.get("id")) == pick)
            save_json(selected, os.path.join(book_dir, "01_selected.json"))
            return selected
        except StopIteration:
            print("无效编号")

def stage_setting_outline(client, config, book_dir, style_card, topic):
    """阶段2：设定+大纲，带试读者评估。"""
    print("\n📋 阶段2：生成设定+大纲...")

    card_json = json.dumps(style_card, ensure_ascii=False, indent=2)
    topic_json = json.dumps(topic, ensure_ascii=False, indent=2)

    def generate_outline(extra_instructions=""):
        """生成大纲；extra_instructions 非空时追加到 prompt 末尾。"""
        base_prompt = load_prompt("02_setting_outline.md",
            style_card_json=card_json, selected_topic_json=topic_json,
            target_words_min=config["story"]["target_words_min"],
            target_words_max=config["story"]["target_words_max"],
        )
        if extra_instructions:
            base_prompt += (
                f"\n\n## 特别注意（基于上一版的读者反馈，本次必须改进）\n{extra_instructions}"
            )

        for attempt in range(2):  # 解析失败自动重试一次
            resp = client.chat(
                messages=[{"role": "user", "content": base_prompt}],
                model=config["models"]["creative"],
                temperature=config["generation"]["temperature_creative"],
                max_tokens=config["generation"]["max_tokens_medium"],
                stage="outline", label="gen" if not extra_instructions else "regen",
                json_mode=True,
            )
            result = extract_json(resp)
            if isinstance(result, dict) and result:
                save_json(result, os.path.join(book_dir, "02_outline.json"))
                if not extra_instructions:
                    save_text(resp, os.path.join(book_dir, "02_outline_raw.md"))
                return result
            print(f"  ⚠ 大纲解析失败，重试（{attempt+1}/2）...")
        # 两次都失败，返回最后一次的原始结果
        return result

    outline = generate_outline()

    # B1: 角色设定合理性检查（同姓/性格单一/关系不清）
    print("  → 角色设定合理性检查...")
    char_result = character_check(client, config, outline, book_dir)
    if char_result.get("needs_fix", False):
        fixes = char_result.get("fix_suggestions", [])
        if fixes:
            fix_str = "\n".join(f"- {f}" for f in fixes)
            print(f"  🔧 角色设定需调整，重新生成大纲...")
            outline = generate_outline(
                extra_instructions=f"## 角色设定修改意见（必须改进后重新输出）\n{fix_str}"
            )

    # 试读者评估大纲，用 regenerate_fn 重新生成代替直接修改
    print("  → 试读者评估大纲节奏...")
    outline_text = json.dumps(outline, ensure_ascii=False, indent=2) if isinstance(outline, dict) else str(outline)
    outline_text, _ = eval_and_fix_loop(
        client, config, outline_text, "大纲（评估故事节奏）",
        book_dir, "outline",
        regenerate_fn=lambda extra_instructions="": json.dumps(
            generate_outline(extra_instructions=extra_instructions),
            ensure_ascii=False, indent=2,
        ),
    )

    # 确保最终 outline 是 dict
    if isinstance(outline_text, str):
        try:
            outline = extract_json(outline_text)
            save_json(outline, os.path.join(book_dir, "02_outline.json"))
        except Exception:
            pass

    return outline

def build_character_cards(outline):
    """从大纲中提取角色语言卡片（含秘密，用于正文阶段伏笔回收）。"""
    chars = outline.get("characters", []) if isinstance(outline, dict) else []
    cards = ""
    for c in chars:
        secret = c.get("secret", "")
        secret_str = f" | 隐藏秘密：{secret}" if secret else ""
        cards += f"【{c.get('name','?')}】{c.get('role','')} | 说话风格：{c.get('speech_style','无')}{secret_str}\n"
    return cards or "（无角色卡片）"

def character_check(client, config, outline, book_dir):
    """角色设定合理性检查（B1）：同姓、性格深度、关系清晰度。"""
    chars = outline.get("characters", []) if isinstance(outline, dict) else []
    if not chars:
        return {}
    prompt = load_prompt("02b_character_check.md",
        characters_json=json.dumps(chars, ensure_ascii=False, indent=2))
    resp = client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=config["models"]["creative"],
        temperature=0.5, max_tokens=1000,
        stage="char_check", label="check",
        json_mode=True,
    )
    result = extract_json(resp)
    save_json(result if isinstance(result, dict) else {}, os.path.join(book_dir, "02b_char_check.json"))
    return result if isinstance(result, dict) else {}

def _extract_face_slap(client, config, part_text, part_index, book_dir):
    """提取本部分用了什么打脸方式（一句话概括），用于注入下一部分防重复。"""
    prompt = (
        "以下是小说的一个片段。请用一句话（20字以内）概括其中最主要的打脸方式。"
        "如果没有打脸场景，回答'无'。只输出这一句话，不要解释。\n\n"
        f"片段：\n{part_text[:3000]}"
    )
    resp = client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=config["models"]["workhorse"],
        temperature=0.3, max_tokens=50,
        stage="extract_slap", label=f"part{part_index}",
    )
    resp = resp.strip()
    if resp and resp != "无":
        return resp
    return ""

def quick_check(client, config, content, previous_summary, book_dir, label):
    """硬伤快检：3个是非题，有硬伤才触发修改。"""
    prompt = load_prompt("03_quick_check.md",
        previous_summary=previous_summary, content=content)
    resp = client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=config["models"]["creative"],
        temperature=0.3, max_tokens=500,
        stage="quick_check", label=label,
        json_mode=True,
    )
    result = extract_json(resp)
    save_json(result, os.path.join(book_dir, f"qc_{label}.json"))

    has_problem = False
    problems = []
    if isinstance(result, dict):
        if not result.get("female_lead_pov", True):
            has_problem = True
            problems.append("女主视角丢失")
        if result.get("face_slap_repeated", False):
            has_problem = True
            problems.append("打脸方式重复")
        if result.get("has_chicken_soup", False):
            has_problem = True
            problems.append("出现鸡汤/感悟")

    if has_problem:
        print(f"  ⚠ 硬伤检出: {', '.join(problems)}，触发修改...")
        fix_prompt = (
            f"以下正文存在这些硬伤：{', '.join(problems)}。请修复：\n"
            f"- 女主视角丢失→把叙事切回女主第一人称'我'\n"
            f"- 打脸方式重复→换一种打脸方式（借力打力/群众打脸/装傻反杀）\n"
            f"- 鸡汤/感悟→删掉所有总结性抒情语句，结尾改成对话或动作\n\n"
            f"原文：\n{content}\n\n直接输出修复后的完整正文。"
        )
        fixed = client.chat(
            messages=[{"role": "user", "content": fix_prompt}],
            model=config["models"]["creative"],
            temperature=config["generation"]["temperature_writing"],
            max_tokens=config["generation"]["max_tokens_long"],
            stage="qc_fix", label=label,
        )
        save_text(fixed, os.path.join(book_dir, f"qc_fix_{label}.md"))
        return fixed
    else:
        print(f"  ✅ 硬伤快检通过")
        return content


def continuity_check(client, config, full_text, book_dir):
    """三段拼接后的全文衔接检查。"""
    print("  → 全文衔接检查...")
    prompt = load_prompt("03_continuity_check.md", full_text=full_text)
    resp = client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=config["models"]["creative"],
        temperature=0.3, max_tokens=2000,
        stage="continuity", label="check",
        json_mode=True,
    )
    result = extract_json(resp)
    save_json(result, os.path.join(book_dir, "continuity_check.json"))
    save_text(resp, os.path.join(book_dir, "continuity_check_raw.md"))

    needs_fix = isinstance(result, dict) and result.get("needs_fix", False)

    if needs_fix:
        print("  🔧 发现衔接问题，局部修复...")
        fix_prompt = load_prompt("03_seam_fix.md",
            check_report_json=json.dumps(result, ensure_ascii=False, indent=2),
            full_text=full_text)
        fixed = client.chat(
            messages=[{"role": "user", "content": fix_prompt}],
            model=config["models"]["creative"],
            temperature=config["generation"]["temperature_writing"],
            max_tokens=config["generation"]["max_tokens_long"],
            stage="continuity", label="fix",
        )
        save_text(fixed, os.path.join(book_dir, "continuity_fixed.md"))
        return fixed
    else:
        print("  ✅ 衔接检查通过")
        return full_text


def _outline_sections(outline):
    """从大纲中提取段落编号列表，用于切分写作范围。"""
    if not isinstance(outline, dict):
        return []
    return [s.get("section", i+1) for i, s in enumerate(outline.get("outline", []))]

def stage_write_story(client, config, book_dir, style_card, outline):
    """阶段3：分4次直写正文（无骨架中间层）。"""
    print("\n✍️ 阶段3：正文直写（共4部分）...")

    card_json = json.dumps(style_card, ensure_ascii=False, indent=2)
    outline_json = json.dumps(outline, ensure_ascii=False, indent=2)
    char_cards = build_character_cards(outline)
    voice_sample = style_card.get("voice_sample", "") if isinstance(style_card, dict) else ""
    target_words = config["story"]["words_per_part"]

    sections = _outline_sections(outline)
    total_sec = len(sections)
    # 按1/4、1/2、3/4切分大纲段落（4等分）
    cut1 = max(1, total_sec // 4)
    cut2 = max(cut1 + 1, total_sec // 2)
    cut3 = max(cut2 + 1, total_sec * 3 // 4)

    parts_meta = [
        (1, "开头到第一次打脸后",
         f"第{sections[0]}段" if sections else "开头",
         f"第{sections[cut1-1]}段" if sections else "第一次打脸后",
         True),
        (2, "第一次打脸后到中段发展",
         f"第{sections[cut1]}段" if cut1 < total_sec else "中段前",
         f"第{sections[cut2-1]}段" if cut2 <= total_sec else "中段",
         False),
        (3, "中段到低谷/转折",
         f"第{sections[cut2]}段" if cut2 < total_sec else "转折前",
         f"第{sections[cut3-1]}段" if cut3 <= total_sec else "低谷",
         False),
        (4, "转折到故事结局",
         f"第{sections[cut3]}段" if cut3 < total_sec else "结尾段",
         "故事结尾",
         False),
    ]

    full_text = ""
    used_face_slaps = []  # 已用过的打脸方式，逐步积累注入后续部分

    for part_index, part_desc, section_from, section_to, is_first in parts_meta:
        print(f"  → 写第{part_index}部分：{part_desc}...")

        if is_first:
            previous_part_block = ""
            part1_note = (
                "\n注意：这是第1部分，也是整个故事的开头。"
                "前3句必须有冲突/反差/悬念，每句一个信息量，直接进入正文，不要任何前言。"
            )
        else:
            previous_part_block = (
                f"## 前文末尾（自然衔接，不重复这些内容）\n\n{full_text[-2000:]}"
            )
            part1_note = ""

        # 构建打脸方式禁用块
        if used_face_slaps:
            used_face_slaps_block = (
                "## 前文已使用过的打脸方式（禁止重复）\n\n"
                + "\n".join(f"- {m}" for m in used_face_slaps)
            )
        else:
            used_face_slaps_block = ""

        prompt = load_prompt("03_write_part.md",
            part_index=part_index,
            part_desc=part_desc,
            section_from=section_from,
            section_to=section_to,
            target_words=target_words,
            style_card_json=card_json,
            outline_json=outline_json,
            character_cards=char_cards,
            voice_sample=voice_sample,
            previous_part_block=previous_part_block,
            part1_note=part1_note,
            used_face_slaps_block=used_face_slaps_block,
        )

        part_text = client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=config["models"]["workhorse"],
            temperature=config["generation"]["temperature_writing"],
            max_tokens=config["generation"]["max_tokens_long"],
            stage="write", label=f"part{part_index}",
        )

        save_text(part_text, os.path.join(book_dir, f"03_part{part_index}.md"))

        if is_first:
            # 第1部分：试读者完整评估
            print("  → 试读者评估开头质量...")
            part_text, _ = eval_and_fix_loop(
                client, config, part_text, "故事开头（约2500字，评估开头吸引力和整体质量）",
                book_dir, "part1"
            )
            save_text(part_text, os.path.join(book_dir, "03_part1_final.md"))
        else:
            # 第2-4部分：硬伤快检
            part_text = quick_check(
                client, config, part_text,
                previous_summary=full_text[-500:],
                book_dir=book_dir, label=f"part{part_index}",
            )

        # 提取本部分打脸方式，注入下一部分防重复
        slap_method = _extract_face_slap(client, config, part_text, part_index, book_dir)
        if slap_method:
            used_face_slaps.append(slap_method)

        full_text += part_text + "\n\n"

    # 四段拼接后做衔接检查
    full_text = continuity_check(client, config, full_text, book_dir)

    save_text(full_text, os.path.join(book_dir, "03_full_draft.md"))
    word_count = len(full_text)
    print(f"  📊 正文完成，约 {word_count} 字")
    return full_text

def stage_polish(client, config, book_dir, draft):
    """阶段4：润色+去AI味。"""
    print("\n✨ 阶段4：润色...")
    
    # 如果全文太长，分两次润色
    if len(draft) > 6000:
        lines = draft.split('\n')
        mid = len(lines) // 2
        for i in range(mid, min(mid+20, len(lines))):
            if lines[i].strip() == '':
                mid = i; break
        
        parts = ['\n'.join(lines[:mid]), '\n'.join(lines[mid:])]
        polished = ""
        for idx, part in enumerate(parts):
            print(f"  → 润色第{idx+1}部分...")
            prompt = load_prompt("04_polish.md", story_text=part)
            result = client.chat(
                messages=[{"role": "user", "content": prompt}],
                model=config["models"]["workhorse"],
                temperature=config["generation"]["temperature_polish"],
                max_tokens=config["generation"]["max_tokens_long"],
                stage="polish", label=f"part{idx+1}",
            )
            polished += result + "\n\n"
    else:
        prompt = load_prompt("04_polish.md", story_text=draft)
        polished = client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=config["models"]["workhorse"],
            temperature=config["generation"]["temperature_polish"],
            max_tokens=config["generation"]["max_tokens_long"],
            stage="polish", label="full",
        )
    
    save_text(polished, os.path.join(book_dir, "04_polished.md"))
    return polished

def finalize(book_dir, style_card, topic, outline, polished):
    """组装最终输出。"""
    print("\n📖 组装最终输出...")
    
    title = "未命名"
    tags = []
    if isinstance(topic, dict):
        title = topic.get("title", title)
    if isinstance(outline, dict):
        title = outline.get("title", title)
        tags = outline.get("tags", [])
    
    # 最终文件
    final = polished.strip()
    save_text(final, os.path.join(book_dir, "final.md"))
    
    # 元数据
    meta = {
        "title": title,
        "title_alt": topic.get("title_alt", "") if isinstance(topic, dict) else "",
        "tags": tags,
        "word_count": len(final),
        "track": style_card.get("track", "") if isinstance(style_card, dict) else "",
        "protagonist_voice": style_card.get("protagonist_voice", "") if isinstance(style_card, dict) else "",
        "generated_at": datetime.now().isoformat(),
    }
    save_json(meta, os.path.join(book_dir, "metadata.json"))
    
    print(f"\n  标题: {title}")
    print(f"  标签: {', '.join(tags)}")
    print(f"  字数: {len(final)}")
    
    return meta


# ── 主流程 ────────────────────────────────────

STAGES = ["style_card", "topic", "outline", "write", "polish", "done"]

def main():
    parser = argparse.ArgumentParser(description="番茄短故事工厂 v2")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--book-name", type=str, default=None)
    args = parser.parse_args()
    
    config = load_config()
    
    # 确定项目目录
    if args.book_name:
        book_name = args.book_name
    elif args.resume:
        dirs = sorted([d for d in os.listdir(config["output"]["dir"])
                       if os.path.isdir(os.path.join(config["output"]["dir"], d))])
        if not dirs:
            print("没有可恢复的项目"); return
        book_name = dirs[-1]
    else:
        book_name = f"story_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    book_dir = os.path.join(config["output"]["dir"], book_name)
    ensure_dir(book_dir)
    
    print(f"\n{'='*60}")
    print(f"📚 番茄短故事工厂 v2")
    print(f"   项目: {book_name}")
    print(f"   目标: {config['story']['target_words_min']}-{config['story']['target_words_max']}字")
    print(f"{'='*60}")
    
    client = ZhipuClient(config)
    progress = Progress(book_dir)
    current = progress.get() if args.resume else "style_card"
    
    # ── 阶段0：风格卡 ──
    if STAGES.index(current) <= STAGES.index("style_card"):
        style_card = stage_style_card(client, config, book_dir, config["output"]["dir"])
        progress.set("topic")
    else:
        style_card = load_json(os.path.join(book_dir, "00_style_card.json"))
    
    # ── 阶段1：选题对喷 ──
    if STAGES.index(current) <= STAGES.index("topic"):
        revised = stage_topic_debate(client, config, book_dir, style_card)
        
        choice = human_pause("选题", os.path.join(book_dir, "01c_revised.json"))
        if choice == 'q': progress.set("topic"); return
        if choice == 'r':
            revised = stage_topic_debate(client, config, book_dir, style_card)
        if choice == 'e':
            revised = load_json(os.path.join(book_dir, "01c_revised.json"))
        
        topic = pick_topic_interactive(revised, book_dir)
        progress.set("outline")
    else:
        topic = load_json(os.path.join(book_dir, "01_selected.json"))
    
    # ── 阶段2：设定+大纲 ──
    if STAGES.index(current) <= STAGES.index("outline"):
        outline = stage_setting_outline(client, config, book_dir, style_card, topic)
        
        choice = human_pause("设定+大纲", os.path.join(book_dir, "02_outline.json"))
        if choice == 'q': progress.set("outline"); return
        if choice == 'r':
            outline = stage_setting_outline(client, config, book_dir, style_card, topic)
        if choice == 'e':
            outline = load_json(os.path.join(book_dir, "02_outline.json"))
        
        progress.set("write")
    else:
        outline = load_json(os.path.join(book_dir, "02_outline.json"))

    # ── 阶段3：正文直写 ──
    if STAGES.index(current) <= STAGES.index("write"):
        print("\n🚀 以下阶段全自动运行...")
        draft = stage_write_story(client, config, book_dir, style_card, outline)
        progress.set("polish")
    else:
        draft = load_text(os.path.join(book_dir, "03_full_draft.md"))
    
    # ── 阶段4：润色 ──
    if STAGES.index(current) <= STAGES.index("polish"):
        polished = stage_polish(client, config, book_dir, draft)
        progress.set("done")
    else:
        polished = load_text(os.path.join(book_dir, "04_polished.md"))
    
    # ── 输出 ──
    meta = finalize(book_dir, style_card, topic, outline, polished)
    
    # 更新已用风格日志
    history = load_style_history(config["output"]["dir"])
    if isinstance(style_card, dict):
        history.append({
            "track": style_card.get("track"),
            "variant": style_card.get("variant"),
            "protagonist_voice": style_card.get("protagonist_voice"),
            "book_name": book_name,
            "date": datetime.now().isoformat(),
        })
        save_style_history(config["output"]["dir"], history)
    
    # Token日志
    client.save_usage_log(os.path.join(book_dir, "token_usage.json"))
    
    print(f"\n{'='*60}")
    print(f"🎉 完成！")
    print(f"   文件: {os.path.join(book_dir, 'final.md')}")
    print(f"   字数: {meta.get('word_count', '?')}")
    print(f"   Token: {client.usage_log['total_tokens']:,}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
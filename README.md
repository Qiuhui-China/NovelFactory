# 📚 番茄短故事工厂 v2

自动生成番茄小说短故事的 pipeline。基于智谱API，每篇约15-20万token。

## 快速开始

```bash
pip install openai pyyaml
```

创建本地配置文件（不要把真实 API Key 提交到 GitHub）。

Windows PowerShell：
```powershell
Copy-Item config.example.yaml config.yaml
```

然后编辑 `config.yaml`，填入你的智谱 API Key。

```powershell
# Windows PowerShell
$env:PYTHONIOENCODING="utf-8"; python pipeline.py
```

## 流程

```
阶段0  主编Agent生成风格卡（赛道+性格+幽默风格）
  ↓
阶段1  双作者对喷选题 → 编辑吐槽 → 修改 → 【你选一个】
  ↓
阶段2  设定+大纲 → 试读者评大纲 → 不合格自动改 → 【你过目】
  ↓
阶段3a 4000字故事骨架 → 试读者评骨架 → 不合格自动改
  ↓
阶段3b 扩写到1万字（分上下两部分）
  ↓
阶段4  润色+去AI味
  ↓
输出   final.md + 标题 + 标签 + token日志
```

## 3大赛道

| 赛道 | 描述 | 标签 |
|------|------|------|
| 女配觉醒 | 穿书/重生成恶毒女配，改命逆袭 | 打脸逆袭+女配+爽文 |
| 身份反转 | 隐藏身份，真相揭开瞬间最爽 | 甜宠+反转+打脸逆袭 |
| 脑洞搞笑 | 荒诞设定，一本正经应对 | 搞笑轻松+脑洞+反转 |

## 文件结构

```
output/story_XXXXXXXX/
├── 00_style_card.json       ← 风格卡
├── 01a_topics.json          ← 作者A的3个方案
├── 01b_critic.json          ← 编辑B的吐槽
├── 01c_revised.json         ← 修改后的方案
├── 01_selected.json         ← 你选的方案
├── 02_outline.json          ← 设定+大纲
├── 03a_skeleton.md          ← 故事骨架
├── 03a_skeleton_final.md    ← 评估后的骨架
├── 03b_full_draft.md        ← 扩写后的全文
├── 04_polished.md           ← 润色后
├── final.md                 ← 最终成品 ★
├── metadata.json            ← 标题+标签+字数
├── eval_*.json              ← 试读者评估记录
├── progress.json            ← 断点续跑进度
└── token_usage.json         ← Token消耗日志
```

## 常用操作

```bash
# 新建一篇
python pipeline.py

# 从断点继续
python pipeline.py --resume

# 指定项目名
python pipeline.py --book-name "test1"
```

## Token消耗估算

每篇约12-20万token（含评估和修改循环）。

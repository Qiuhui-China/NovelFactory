# 全文衔接检查

你是番茄小说编辑，通读以下完整故事，检查各段拼接后的衔接问题。

## 完整正文

{full_text}

## 请用JSON回答

```json
{
  "seam_issues": [
    {
      "location": "第1-2部分衔接处（大约第X段）",
      "problem": "具体描述衔接问题（如重复、断裂、情绪不连贯、突然冒出新角色）",
      "fix": "如何修（具体到改哪句话）"
    }
  ],
  "character_drift": {
    "found": false,
    "detail": "如果有，描述哪个角色的说话风格在哪里开始变味"
  },
  "face_slap_escalation": {
    "ok": true,
    "detail": "打脸是否递增？如果重复了，指出哪两次重复"
  },
  "needs_fix": false
}
```

如果没有任何问题，`seam_issues` 填空数组 `[]`，`needs_fix` 填 `false`。

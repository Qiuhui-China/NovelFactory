# 角色设定合理性检查

你是一位经验丰富的小说编辑，检查以下角色设定是否存在问题。

## 角色列表

{characters_json}

## 检查项（JSON输出）

**JSON规范：所有字符串值只用双引号，引用词语用「」而非""，禁止在字符串值内部出现未转义的双引号。**

```json
{
  "surname_conflict": {
    "found": false,
    "detail": "是否有不相关的角色撞姓？亲子关系共姓合理，其他角色应避免同姓（除非剧情需要）"
  },
  "personality_depth": [
    {
      "name": "角色名",
      "has_dual_side": true,
      "suggestion": "如果性格太单一（纯好人/纯坏人），建议增加什么矛盾面？例如：反派虽然狠毒但对某个人有软肋；好人虽然善良但有致命弱点"
    }
  ],
  "relationship_clarity": {
    "ok": true,
    "detail": "角色之间的关系是否清晰？有没有角色的存在感不明确？"
  },
  "needs_fix": false,
  "fix_suggestions": ["具体修改建议1", "具体修改建议2"]
}
```

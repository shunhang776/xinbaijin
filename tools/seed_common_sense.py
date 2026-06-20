"""
社交直觉经验库种子脚本 — AI Native 冷启动用。
白槿的初始社交经验，后续由 _reflect_and_learn 自动生长。
人工只在极端偏差时用此脚本手动修正。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory_engine.common_sense import add_experience, list_experiences

# 初始种子经验（冷启动兜底，后续自动生长）
SEEDS = [
    {
        "scene": "对方说在吃饭、洗澡、开车、出门的时候",
        "intuition": "对方不方便长时间看手机，说话要简短一点，或者等对方忙完再说",
        "note": "初始经验",
    },
    {
        "scene": "对方很开心、一直在笑、分享有趣的事",
        "intuition": "对方在分享快乐，要接住这份情绪，不要跳过情绪直接分析",
        "note": "初始经验",
    },
    {
        "scene": "对方心情不好、倾诉烦恼、或者问'你觉得我怎么样'这种敏感问题",
        "intuition": "对方需要的是被理解和共情，不要急于给解决方案或判断对错",
        "note": "初始经验",
    },
    {
        "scene": "对方用'哈哈哈哈'、'笑死'等大量语气词表达情绪",
        "intuition": "对方处于强烈的开心状态，先回应这份快乐，不用急着分析原因",
        "note": "eval补充",
    },
    {
        "scene": "对方提到'昨天不是说好'、'上次答应'等之前的事",
        "intuition": "这件事在对方心里有延续性，先回应'我记得'、'一直想着'，再接当下内容",
        "note": "eval补充",
    },
]

if __name__ == "__main__":
    before = len(list_experiences())
    added = 0
    for s in SEEDS:
        add_experience(s["scene"], s["intuition"], s.get("note", ""))
        added += 1
    after = len(list_experiences())
    print(f"种子经验灌入完成: {before} → {after} (+{added})")
    print("\n当前经验库:")
    for i, e in enumerate(list_experiences()):
        print(f"  {i+1}. {e['scene'][:50]} → {e['intuition'][:50]}")

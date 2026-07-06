"""密码重置工具 —— 忘记密码时把系统密码重置为默认值

业界做法参考：
- Linux：单用户模式 + passwd 命令
- MySQL：--skip-grant-tables 启动 + UPDATE user
- 路由器：长按 Reset 按钮

共同点：需要"物理/服务器访问权限"才能重置，重置后强制改回强密码。
本脚本要求在服务器上本地执行，重置为默认密码 123456，
重启服务后系统会检测到 is_default=true 强制要求修改新密码。

使用方法：
    python reset_password.py

依赖：bcrypt（已在 requirements.txt 中）
"""
import json
import os
import sys
import tempfile
from datetime import datetime


PASSWORD_FILE = os.path.join(tempfile.gettempdir(), "gateway_ai_password.json")
DEFAULT_PASSWORD = "123456"


def main():
    print("=" * 60)
    print(" 网闸AI设备智能体 -- 密码重置工具")
    print("=" * 60)
    print()
    print(f"密码文件位置: {PASSWORD_FILE}")
    print(f"文件是否存在: {'是' if os.path.exists(PASSWORD_FILE) else '否'}")
    print()
    print("[警告] 本操作将把系统密码重置为默认值 123456")
    print("       重置后请立即重启服务，登录时会强制要求修改为新密码")
    print()

    confirm = input("确认重置？请输入 YES 继续，其他输入退出: ").strip()
    if confirm != "YES":
        print("已取消")
        return

    try:
        import bcrypt
    except ImportError:
        print("[错误] 缺少 bcrypt 库，请先执行: pip install bcrypt")
        sys.exit(1)

    hashed = bcrypt.hashpw(DEFAULT_PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    data = {
        "hash": hashed,
        "is_default": True,
        "updated_at": datetime.now().isoformat(),
        "reset_by_tool": True,
    }

    try:
        with open(PASSWORD_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print()
        print("[完成] 密码已重置为默认值: 123456")
        print("       请重启服务，登录后系统会强制要求修改为新密码")
        print()
        print("重置步骤：")
        print("  1. 关闭当前运行的网闸智能体服务（Ctrl+C）")
        print("  2. 重新启动服务")
        print("  3. 浏览器打开管理平台，系统会检测到默认密码并弹窗要求修改")
    except Exception as e:
        print(f"[错误] 写入密码文件失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
将指定路径下的xmi文件插入项目中
"""

import os
import glob
import subprocess
import time

# ========== 配置区域 ==========
# 使用 .bat 而不是 .exe
XMI4RHAPSODY_BAT = r"C:\LegacyApp\Rhapsody_902_64bit\Sodius\XMI_Toolkit\bin\XMI4Rhapsody.bat"
XMI_FOLDER = r".\xmi_read"          # 存放所有 .xmi 文件的文件夹
LOG_DIR = r".\xmi_import_logs"  # 可选：单独存放每个导入的日志
# =============================

def batch_import():
    if not os.path.isfile(XMI4RHAPSODY_BAT):
        print(f"错误：找不到 {XMI4RHAPSODY_BAT}")
        return

    xmi_files = glob.glob(os.path.join(XMI_FOLDER, "*.xmi"))
    if not xmi_files:
        print(f"在 {XMI_FOLDER} 中没有找到 .xmi 文件")
        return

    # 确保日志目录存在
    os.makedirs(LOG_DIR, exist_ok=True)

    success_count = 0
    for i, xmi_path in enumerate(xmi_files, 1):
        print(f"\n[{i}/{len(xmi_files)}] 正在导入: {os.path.basename(xmi_path)}")

        # 构建命令参数（注意：-silent true 必须紧跟在命令后，参数之间用空格分隔）
        cmd = [
            XMI4RHAPSODY_BAT,
            "-silent", "true",
            "-mode", "IMPORT",
            "-format", "uml21",
            "-xmi", xmi_path
        ]


        try:
            # 执行命令，捕获输出（可选，但建议重定向到日志文件）
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                env=os.environ.copy()  # 继承当前环境变量
            )
            if result.returncode == 0:
                print(f"  ✓ 成功")
                success_count += 1
            else:
                print(f"  ✗ 失败，返回码 {result.returncode}")
                # 将错误输出保存到单独的日志文件
                log_file = os.path.join(LOG_DIR, f"{os.path.basename(xmi_path)}.log")
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write(f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}")
                print(f"    详细日志已保存至: {log_file}")
        except subprocess.TimeoutExpired:
            print(f"  ✗ 超时（>600秒）")
        except Exception as e:
            print(f"  ✗ 异常: {e}")

        time.sleep(1)  # 避免资源冲突

    print(f"\n批量导入完成。成功: {success_count}/{len(xmi_files)}")

if __name__ == "__main__":
    # ⚠️ 重要：在运行脚本前，请确保：
    # 1. Rhapsody GUI 已经打开，并且目标项目已加载。
    # 2. XMI Toolkit 插件模式已激活（文档 10.1 节）—— 虽然静默模式不强制要求插件模式，但激活后性能更好。
    batch_import()

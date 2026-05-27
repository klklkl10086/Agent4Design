from pydantic import BaseModel

import os
import glob
import subprocess


class XMIImportResult(BaseModel):
    xmi_path: str
    success: bool
    return_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    log_path: str | None = None

    


def import_xmi_file(
    xmi_path: str,
    toolkit_bat: str,
    log_dir: str = "xmi_import_logs",
    timeout: int = 600,
) -> XMIImportResult:
    if not os.path.isfile(toolkit_bat):
        return XMIImportResult(xmi_path=xmi_path, success=False, stderr=f"Toolkit not found: {toolkit_bat}")

    if not os.path.isfile(xmi_path):
        return XMIImportResult(xmi_path=xmi_path, success=False, stderr=f"XMI file not found: {xmi_path}")
    
    cmd = [
            toolkit_bat,
            "-silent", "true",
            "-mode", "IMPORT",
            "-format", "uml21",
            "-xmi", xmi_path
        ]
    try:
        os.makedirs(log_dir, exist_ok=True)
        # 执行命令，捕获输出（可选，但建议重定向到日志文件）
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy()  # 继承当前环境变量
        )
        code = result.returncode
        if code == 0:
            return XMIImportResult(
                xmi_path=xmi_path,
                success=True,
                return_code=code,
                stdout=result.stdout,
                stderr=result.stderr,
                )
        else:
            log_file = os.path.join(log_dir, f"{os.path.basename(xmi_path)}.log")
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}")
            return XMIImportResult(
                xmi_path=xmi_path,
                success=False,
                return_code=code,
                stdout=result.stdout,
                stderr=result.stderr,
                log_path=log_file)
        
    
    except subprocess.TimeoutExpired as e:
        return XMIImportResult(
            xmi_path=xmi_path,
            success=False,
            stderr=f"Import timed out after {timeout}s",
        )
    except Exception as e:
        return XMIImportResult(
            xmi_path=xmi_path,
            success=False,
            stderr=str(e),
        )

        

            
def import_xmi_folder(
    xmi_folder: str,
    toolkit_bat: str,
    log_dir: str = "xmi_import_logs",
) -> list[XMIImportResult]:
    if not os.path.isfile(toolkit_bat):
        return []
        
    xmi_files = glob.glob(os.path.join(xmi_folder, "*.xmi"))
    if not xmi_files:
        return []
    
    os.makedirs(log_dir, exist_ok=True)

    res = []
    for i, xmi_path in enumerate(xmi_files, 1):
        print(f"\n[{i}/{len(xmi_files)}] 正在导入: {os.path.basename(xmi_path)}")
        r = import_xmi_file(xmi_path,toolkit_bat,log_dir)
        res.append(r)

    return res

        
    

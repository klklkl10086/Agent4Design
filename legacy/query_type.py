# -*- coding: utf-8 -*-
"""
Rhapsody Type Cache Script

功能：
1. 连接到 Rhapsody COM 接口，获取当前活动项目。
2. 递归遍历项目中的所有包，收集所有类型元素（IRPType / IRPClassifier）的
   全路径（getFullPathName）和元类名称（metaClass）。
3. 将收集到的类型信息保存为 JSON 文件。
4. 从 JSON 文件中读取类型信息，并在当前 Rhapsody 项目中重新定位对应的类型对象，
   以便在脚本中快速设置操作参数类型或返回值类型。

使用方法：
- 运行脚本前请确保 Rhapsody 已打开并加载了目标项目。
- 第一次运行时调用 save_types_to_file() 生成缓存文件。
- 后续运行时调用 load_types_from_cache() 获取类型字典，直接使用。
"""

import json
import os
from win32com.client import Dispatch, DispatchBaseClass

# ----------------------------- 收集类型信息 -----------------------------
def collect_all_types(project):
    """
    遍历项目中的所有包，返回一个列表，每个元素为 (fullPath, metaClass)
    """
    result = []

    def process_package(pkg):
        # 收集包内的 Type（如 typedef、枚举）
        try:
            for t in pkg.types:
                path = t.getFullPathName()
                meta = t.metaClass
                result.append((path, meta))
        except Exception as e:
            print(f"Warning: failed to process types in {pkg.name}: {e}")

        # 收集包内的 Classifier（Class, Actor, Interface 等）
        try:
            for cls in pkg.nestedClassifiers:
                path = cls.getFullPathName()
                meta = cls.metaClass
                result.append((path, meta))
        except Exception as e:
            print(f"Warning: failed to process nestedClassifiers in {pkg.name}: {e}")

        # 递归处理子包
        try:
            for sub in pkg.packages:
                process_package(sub)
        except Exception as e:
            print(f"Warning: failed to process sub-packages in {pkg.name}: {e}")

    # 获取项目的根包（通常为第一个 Package）
    root_packages = project.getNestedElementsByMetaClass("Package", 0)  # 0 = 非递归
    if root_packages is None or root_packages.Count == 0:
        print("No root package found in project.")
        return result

    # 处理每个顶层包
    for i in range(1, root_packages.Count + 1):
        root_pkg = root_packages.Item(i)
        process_package(root_pkg)

    return result


def save_types_to_file(project, filename="rhapsody_types_cache.json"):
    """收集项目中所有类型并保存到 JSON 文件"""
    types_data = collect_all_types(project)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(types_data, f, indent=2)
    print(f"Saved {len(types_data)} type entries to {filename}")
    return types_data


# ----------------------------- 从缓存加载类型 -----------------------------
def get_type_from_storage(project, full_path, meta_class):
    """
    根据存储的全路径和元类名称，在项目中重新获取类型元素。
    返回 IRPModelElement 或 None
    """
    matches = project.findElementsByFullName(full_path, meta_class)
    if matches and matches.Count > 0:
        return matches.Item(1)
    return None


def load_types_from_cache(project, filename="rhapsody_types_cache.json"):
    """
    从 JSON 文件读取类型信息，并在当前项目中定位类型对象。
    返回字典：{ full_path: IRPModelElement }
    """
    if not os.path.exists(filename):
        print(f"Cache file {filename} not found. Please run save_types_to_file first.")
        return {}

    with open(filename, "r", encoding="utf-8") as f:
        types_data = json.load(f)

    type_cache = {}
    for full_path, meta_class in types_data:
        obj = get_type_from_storage(project, full_path, meta_class)
        if obj:
            type_cache[full_path] = obj
        else:
            print(f"Warning: Cannot locate {full_path} ({meta_class})")
    print(f"Loaded {len(type_cache)} type objects from cache.")
    return type_cache


# ----------------------------- 示例：如何使用缓存 -----------------------------
def demo_usage():
    """示例：连接 Rhapsody，保存缓存或加载缓存，并创建一个操作并使用缓存的类型"""
    # 1. 连接到 Rhapsody 应用程序
    try:
        app = Dispatch("Rhapsody2.Application")
    except Exception as e:
        print("Failed to connect to Rhapsody COM. Make sure Rhapsody is running.")
        return

    # 2. 获取当前活动项目
    project = app.activeProject()
    if project is None:
        print("No active project. Please open a project in Rhapsody.")
        return

    # 3. 选择操作：保存缓存或加载缓存
    choice = input("Enter 'save' to cache types, or 'load' to use cached types: ").strip().lower()
    if choice == "save":
        save_types_to_file(project)
        return
    elif choice == "load":
        type_cache = load_types_from_cache(project)
        if not type_cache:
            return
        print("Cache loaded. You can now use type_cache in your automation.")
        # 示例：创建一个类并添加操作，设置参数类型
        # 假设我们有一个名为 "MyClass" 的类（需提前存在）
        my_class = project.findElementsByFullName("MyClass", "Class")
        if my_class and my_class.Count > 0:
            cls = my_class.Item(1)
            op = cls.addOperation("testFunc")
            arg = op.addArgument("input")
            # 从缓存中获取一个类型（比如 "int"）
            int_type = type_cache.get("int")   # 前提是 int 已经被缓存
            if int_type:
                arg.type = int_type
                print("Set argument type to int using cached type.")
            else:
                print("Type 'int' not found in cache.")
            # 设置返回值类型
            ret_type = type_cache.get("void")
            if ret_type:
                op.returnType = ret_type
                print("Set return type to void.")
        else:
            print("Class 'MyClass' not found. Please adjust the demo code.")
    else:
        print("Invalid choice. Please run again with 'save' or 'load'.")


if __name__ == "__main__":
    demo_usage()
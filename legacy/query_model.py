# import os
# import re
# import win32com.client
# import pythoncom
#
# # 连接
# pythoncom.CoInitialize()
#
# try:
#     app = win32com.client.GetActiveObject("Rhapsody2.Application")#返回 IRPApplication 类
# except Exception as e:
#     raise RuntimeError(f"Rhapsody 环境异常: {e}")
#
#
# app_name, rcp_name = app.getApplicationName()
#
# pro = app.activeProject()#return IRPProject(class)
# packclass = pro.packages # COMObject IRPCollection
#
# pkglist= [] #IRPPackage
# count = packclass.Count
# for i in range(1, count + 1):
#     pkg = packclass.Item(i) #profile
#     pkglist.append(pkg)
#
# for pkg in pkglist:
#     if pkg.name == "Design":
#         design_element = pkg.getNestedElements() #IRPCollection
#
# count = design_element.Count
#
# design_list = []
# for i in range(1, count + 1):
#     pkg = design_element.Item(i) #IRPPackage
#     design_list.append(pkg)
# for pkg in design_list:
#     if pkg.name == "Project":
#         project_element = pkg.getNestedElements()
# count = project_element.Count
# cpplist=[]
# for i in range(1, count + 1):
#     cpplist.append(project_element.Item(i)) #IRPPackage
# cpp_file = cpplist[1].modules
#
# file = cpplist[1].modules.Item(1) #IRPModule
# file_element = file.getNestedElements()
#
# count =file_element.Count
# list=[]
# for i in range(1, count + 1):
#     list.append(file_element.Item(i)) #IRPClass

import os
import re
import os
import numpy as np
from dotenv import load_dotenv


# Load environment variables from .env file
load_dotenv()

# Get API token from environment variables
API_TOKEN = os.getenv("API_TOKEN")

# Base URL for the API
BASE_URL = "https://vio.automotive-wan.com:446"

# Common headers
HEADERS = {
    "useLegacyCompletionsEndpoint": "false",
    "X-Tenant-ID": "default_tenant"
}

def list_available_models():
    """
    List all available models from the VIO API.

    Returns:
        list: List of available model IDs
    """
    try:

        import openai
        client = openai.OpenAI(
            api_key=API_TOKEN,
            base_url=BASE_URL,
            default_headers=HEADERS
        )
        models = client.models.list()
        return [model.id for model in models.data]
    except Exception as e:
        print(f"Error listing models: {str(e)}")
        return []

if __name__ == "__main__":
    print(list_available_models())
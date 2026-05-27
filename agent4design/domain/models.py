"""
基本的数据结构定义
  - `CTypeInfo`
  - `FunctionArgument`
  - `MacroSpec`
  - `VariableSpec`
  - `FunctionSpec`
  - `ActivityNode`
  - `ActivityEdge`
  - `ActivityGraph`
  author: Li,Zhiying
  data:2026/5/27
"""
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal

class CTypeInfo(BaseModel):
    """CTypeInfo,当前代码中使用到的数据类型"""
    base_type:str = Field(...,description="变量声明去除所有修饰符后的基础类型,如T_UBYTE")
    is_const:bool=False
    is_static:bool = False
    pointer_modifier: str = ""
    array_multiplicity: str = ""
    raw_declaration: str = ""


class FunctionArgument(BaseModel):
    """FunctionArgument 函数参数相关的数据定义"""
    name:str = ""
    type_info: CTypeInfo

class FunctionSpec(BaseModel):
    """函数签名"""
    name:str
    arguments: list[FunctionArgument] = Field(default_factory=list, description="函数参数列表")
    return_type_info:CTypeInfo 

class MacroSpec(BaseModel):
    """宏定义相关的数据定义"""
    name:str 
    type_info: CTypeInfo | None = None 
    value:str = ""
    raw_declaration: str = ""

class VariableSpec(BaseModel):
    """变量定义相关的数据定义"""
    name:str
    type_info : CTypeInfo
    initial_value: str | None = None
    raw_declaration: str = ""

class ActivityNode(BaseModel):
    id: str = Field(..., description="节点唯一 ID,只使用英文字母、数字和下划线,例如 n1、decision_1、return_ok")
    type: Literal["Initial", "Action", "Decision", "Merge", "Final"] = Field(
        ...,
        description="activity node's type"
    )
    label: str = Field("", description="图上显示的短文本")
    description: str = Field("", description="节点对应的代码片段或语义说明")

class ActivityEdge(BaseModel):
    source:str 
    target:str
    guard: str=Field("",description="如果起点是decision需要填写true,false或者switch具体的case")

class ActivityGraph(BaseModel):
    nodes: list[ActivityNode] = Field(default_factory=list)
    edges:list[ActivityEdge]=Field(default_factory=list)

"""
根据code生成xmi文件
  author: Li,Zhiying
  data:2026/5/27
"""
import os
import uuid
import re


from domain import models
from typing import Optional, List, Dict, Any, Tuple
from jinja2 import Template



# 官方模板
XMI_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<xmi:XMI xmi:version="2.1" xmlns:uml="http://schema.omg.org/spec/UML/2.1" xmlns:xmi="http://schema.omg.org/spec/XMI/2.1">
  <uml:Package xmi:id="Pkg_tmp" name="{{ function_name }}">
    <packagedElement xmi:type="uml:Activity" xmi:id="{{ act_id }}" name="activity_{{ function_name }}">

      {% for node in nodes %}
      {% if node.type == 'Action' %}
      <node xmi:type="uml:OpaqueAction" xmi:id="{{ node.id }}" name="{{ node.name }}">
        <body>{{ node.desc | e }}</body>
      </node>
      {% elif node.type == 'Decision' %}
      <node xmi:type="uml:DecisionNode" xmi:id="{{ node.id }}" name="{{ node.name }}"/>
      {% elif node.type == 'Merge' %}
      <node xmi:type="uml:MergeNode" xmi:id="{{ node.id }}" name="{{ node.name }}"/>
      {% elif node.type == 'Initial' %}
      <node xmi:type="uml:InitialNode" xmi:id="{{ node.id }}" name="{{ node.name }}"/>
      {% elif node.type == 'Final' %}
      <node xmi:type="uml:ActivityFinalNode" xmi:id="{{ node.id }}" name="{{ node.name }}"/>
      {% endif %}
      {% endfor %}

      {% for edge in edges %}
      <edge xmi:type="uml:ControlFlow" xmi:id="{{ edge.id }}" name="{{ loop.index0 }}" source="{{ edge.source }}" target="{{ edge.target }}">
        {% if edge.guard %}
        <guard xmi:type="uml:LiteralString" xmi:id="{{ edge.id }}_guard" value="{{ edge.guard | e }}"/>
        {% endif %}
        <weight xmi:type="uml:LiteralInteger" xmi:id="{{ edge.id }}_weight" value="1"/>
      </edge>
      {% endfor %}

    </packagedElement>
  </uml:Package>
</xmi:XMI>
"""

def sanitize(raw: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", raw or "")
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "unnamed"
    if cleaned[0].isdigit():
        cleaned = f"n_{cleaned}"
    return cleaned
    




def generate_activity_xmi(
        function_spec:models.FunctionSpec,
        graph:models.ActivityGraph,
        output_dir:str
)->str:
  """提取C代码逻辑并直接生成符合 Rhapsody 标准的 XMI 文件，替代手动绘图"""
  function_name = sanitize(function_spec.name)
  graph_dict = graph.model_dump()

  # 0. 确认图的正确性
  # 1. 把节点转换成 XMI 节点数据 nodes_data
  act_id = f"GUID_{uuid.uuid4().hex}"
  nodes_data = []
  for n in graph_dict.get("nodes", []):
      nid = sanitize(n["id"])
      nodes_data.append({
          "id": f"Node_{nid}",
          "type": n["type"],
          "name": f"{n['type']}_{nid}",
          "desc": n.get("description")
      })
   # 2. 把有向边转换成 XMI 节点数据 edges_data
  edges_data = []
  for idx, e in enumerate(graph_dict.get("edges", [])):
      s = e.get("source")
      t = e.get("target")
      guard = e.get("guard")
      
      edges_data.append({
          "id": f"Edge_{idx}_{uuid.uuid4().hex[:6]}",
          "source": f"Node_{sanitize(s)}",
          "target": f"Node_{sanitize(t)}",
          "guard": guard
      })

  # 2. 渲染 Jinja2 XMI 模板
  template = Template(XMI_TEMPLATE)
  xmi_content = template.render(
      function_name=function_name,
      act_id=act_id,
      nodes=nodes_data,
      edges=edges_data
  )

  # 3. 输出并保存XMI
  os.makedirs(output_dir, exist_ok=True)
  xmi_path = os.path.join(output_dir, f"{function_name}.xmi")
  with open(xmi_path, "w", encoding="utf-8") as f:
      f.write(xmi_content)
  return xmi_path

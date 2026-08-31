#!/usr/bin/env python3
# coding: utf-8
# File: MedicalGraph.py
# Author: lhy<lhy_in_blcu@126.com,https://huangyong.github.io>
# Date: 18-10-3
#
# 阶段二加固：
# 1) 数据库密码不再硬编码：优先读环境变量 NEO4J_PASSWORD（自动加载 backend/.env），
#    未提供时显式报错退出，避免使用弱密码兜底；
# 2) 建图前先执行幂等的索引/约束检查（core.graph_index.ensure_graph_indexes）；
# 3) 新增 --index-only 参数：只建索引/约束不导入数据；
# 4) 关系创建改为参数化 Cypher（原先 % 字符串拼接存在注入与转义隐患）。
#
# 阶段三修复（建图幂等）：
# 5) 节点与关系创建全部改为参数化 MERGE（原 g.create/CREATE 为 CREATE 语义）：
#    - 节点 MERGE 以「标签 + name」为匹配键，其余属性仅在新建时写入（ON CREATE SET）；
#    - 关系 MERGE 以「两端节点 + 关系类型」为匹配键，关系属性仅在新建时写入；
#    - MERGE 命中已有节点/关系时直接匹配不新建，因此全新库首跑、存量库重跑均不会
#      因唯一约束冲突而崩溃，也不会产生重复节点与重复边（真正的幂等保护机制，
#      替代旧注释中“数据库拒绝=幂等保护”的错误说法——CREATE 遇约束只会抛异常中断）。
#    注意：逐条 MERGE 在 30 万关系规模上较慢，属既有模式的已知取舍，本次不做批量化重构。

import argparse
import json
import logging
import os
import sys

from py2neo import Graph

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("build_medicalgraph")

# Windows 控制台默认 GBK 编码，建图过程大量中文实体名输出会触发 UnicodeEncodeError，
# 统一把标准输出/错误流切换为 UTF-8（errors=replace 兜底，保证日志不中断建图）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 复用后端集中配置：先加载 backend/.env 再做安全校验（未配置 NEO4J_PASSWORD 时拒绝运行）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
from core.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD  # noqa: E402
from core.graph_index import ensure_graph_indexes  # noqa: E402


class MedicalGraph:
    def __init__(self):
        cur_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_path = os.path.join(cur_dir, 'data/medical.json')
        # 连接信息统一来自环境变量（经 core.config 加载校验），不再硬编码密码；
        # NEO4J_PASSWORD 未配置时 core.config 已直接报错退出，不会走到这里
        self.g = Graph(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    '''建图前幂等地建立索引/唯一约束（重名标签自动降级为普通索引并告警）'''
    def ensure_indexes(self):
        def _runner(cypher, params=None):
            # py2neo 的 run 返回游标，统一转为 dict 列表，满足 ensure_graph_indexes 约定
            return [dict(record) for record in self.g.run(cypher, params or {})]

        logger.info("开始检查/建立图谱索引与唯一约束（幂等）...")
        return ensure_graph_indexes(_runner)

    '''读取文件'''
    def read_nodes(self):
        # 共７类节点
        drugs = [] # 药品
        foods = [] #　食物
        checks = [] # 检查
        departments = [] #科室
        producers = [] #药品大类
        diseases = [] #疾病
        symptoms = []#症状

        disease_infos = []#疾病信息

        # 构建节点实体关系
        rels_department = [] #　科室－科室关系
        rels_noteat = [] # 疾病－忌吃食物关系
        rels_doeat = [] # 疾病－宜吃食物关系
        rels_recommandeat = [] # 疾病－推荐吃食物关系
        rels_commonddrug = [] # 疾病－通用药品关系
        rels_recommanddrug = [] # 疾病－热门药品关系
        rels_check = [] # 疾病－检查关系
        rels_drug_producer = [] # 厂商－药物关系

        rels_symptom = [] #疾病症状关系
        rels_acompany = [] # 疾病并发关系
        rels_category = [] #　疾病与科室之间的关系


        count = 0
        for data in open(self.data_path, encoding='utf-8'):
            disease_dict = {}
            count += 1
            print(count)
            data_json = json.loads(data)
            disease = data_json['name']
            disease_dict['name'] = disease
            diseases.append(disease)
            disease_dict['desc'] = ''
            disease_dict['prevent'] = ''
            disease_dict['cause'] = ''
            disease_dict['easy_get'] = ''
            disease_dict['cure_department'] = ''
            disease_dict['cure_way'] = ''
            disease_dict['cure_lasttime'] = ''
            disease_dict['symptom'] = ''
            disease_dict['cured_prob'] = ''

            if 'symptom' in data_json:
                symptoms += data_json['symptom']
                for symptom in data_json['symptom']:
                    rels_symptom.append([disease, symptom])

            if 'acompany' in data_json:
                for acompany in data_json['acompany']:
                    rels_acompany.append([disease, acompany])

            if 'desc' in data_json:
                disease_dict['desc'] = data_json['desc']

            if 'prevent' in data_json:
                disease_dict['prevent'] = data_json['prevent']

            if 'cause' in data_json:
                disease_dict['cause'] = data_json['cause']

            if 'get_prob' in data_json:
                disease_dict['get_prob'] = data_json['get_prob']

            if 'easy_get' in data_json:
                disease_dict['easy_get'] = data_json['easy_get']

            if 'cure_department' in data_json:
                cure_department = data_json['cure_department']
                if len(cure_department) == 1:
                     rels_category.append([disease, cure_department[0]])
                if len(cure_department) == 2:
                    big = cure_department[0]
                    small = cure_department[1]
                    rels_department.append([small, big])
                    rels_category.append([disease, small])

                disease_dict['cure_department'] = cure_department
                departments += cure_department

            if 'cure_way' in data_json:
                disease_dict['cure_way'] = data_json['cure_way']

            if  'cure_lasttime' in data_json:
                disease_dict['cure_lasttime'] = data_json['cure_lasttime']

            if 'cured_prob' in data_json:
                disease_dict['cured_prob'] = data_json['cured_prob']

            if 'common_drug' in data_json:
                common_drug = data_json['common_drug']
                for drug in common_drug:
                    rels_commonddrug.append([disease, drug])
                drugs += common_drug

            if 'recommand_drug' in data_json:
                recommand_drug = data_json['recommand_drug']
                drugs += recommand_drug
                for drug in recommand_drug:
                    rels_recommanddrug.append([disease, drug])

            if 'not_eat' in data_json:
                not_eat = data_json['not_eat']
                for _not in not_eat:
                    rels_noteat.append([disease, _not])

                foods += not_eat
                do_eat = data_json['do_eat']
                for _do in do_eat:
                    rels_doeat.append([disease, _do])

                foods += do_eat
                recommand_eat = data_json['recommand_eat']

                for _recommand in recommand_eat:
                    rels_recommandeat.append([disease, _recommand])
                foods += recommand_eat

            if 'check' in data_json:
                check = data_json['check']
                for _check in check:
                    rels_check.append([disease, _check])
                checks += check
            if 'drug_detail' in data_json:
                drug_detail = data_json['drug_detail']
                producer = [i.split('(')[0] for i in drug_detail]
                rels_drug_producer += [[i.split('(')[0], i.split('(')[-1].replace(')', '')] for i in drug_detail]
                producers += producer
            disease_infos.append(disease_dict)
        return set(drugs), set(foods), set(checks), set(departments), set(producers), set(symptoms), set(diseases), disease_infos,\
               rels_check, rels_recommandeat, rels_noteat, rels_doeat, rels_department, rels_commonddrug, rels_drug_producer, rels_recommanddrug,\
               rels_symptom, rels_acompany, rels_category

    '''建立节点（幂等）：MERGE 以「标签+name」为匹配键，已存在则跳过不新建。
    标签来自脚本内固定白名单调用（非外部输入），以花括号模板拼接；实体名参数化传入'''
    def create_node(self, label, nodes):
        count = 0
        query = "MERGE (n:%s {name: $name})" % label
        for node_name in nodes:
            self.g.run(query, {"name": node_name})
            count += 1
            print(count, len(nodes))
        return

    '''创建知识图谱中心疾病的节点（幂等）：MERGE 以 name 为匹配键，
    其余属性只在新建节点时写入（ON CREATE SET），重跑不覆盖、不重复。
    重名疾病（如「胎膜早破」语料中出现两次）第二次会命中同一节点而不新建，
    因此无论是否存在唯一约束都不会崩溃'''
    def create_diseases_nodes(self, disease_infos):
        count = 0
        query = (
            "MERGE (n:Disease {name: $name}) "
            "ON CREATE SET n.desc = $desc, n.prevent = $prevent, n.cause = $cause, "
            "n.easy_get = $easy_get, n.cure_lasttime = $cure_lasttime, "
            "n.cure_department = $cure_department, n.cure_way = $cure_way, "
            "n.cured_prob = $cured_prob"
        )
        for disease_dict in disease_infos:
            self.g.run(query, {
                "name": disease_dict['name'],
                "desc": disease_dict['desc'],
                "prevent": disease_dict['prevent'],
                "cause": disease_dict['cause'],
                "easy_get": disease_dict['easy_get'],
                "cure_lasttime": disease_dict['cure_lasttime'],
                "cure_department": disease_dict['cure_department'],
                "cure_way": disease_dict['cure_way'],
                "cured_prob": disease_dict['cured_prob'],
            })
            count += 1
            print(count)
        return

    '''创建知识图谱实体节点类型schema'''
    def create_graphnodes(self):
        Drugs, Foods, Checks, Departments, Producers, Symptoms, Diseases, disease_infos,rels_check, rels_recommandeat, rels_noteat, rels_doeat, rels_department, rels_commonddrug, rels_drug_producer, rels_recommanddrug,rels_symptom, rels_acompany, rels_category = self.read_nodes()
        self.create_diseases_nodes(disease_infos)
        self.create_node('Drug', Drugs)
        print(len(Drugs))
        self.create_node('Food', Foods)
        print(len(Foods))
        self.create_node('Check', Checks)
        print(len(Checks))
        self.create_node('Department', Departments)
        print(len(Departments))
        self.create_node('Producer', Producers)
        print(len(Producers))
        self.create_node('Symptom', Symptoms)
        return


    '''创建实体关系边'''
    def create_graphrels(self):
        Drugs, Foods, Checks, Departments, Producers, Symptoms, Diseases, disease_infos, rels_check, rels_recommandeat, rels_noteat, rels_doeat, rels_department, rels_commonddrug, rels_drug_producer, rels_recommanddrug,rels_symptom, rels_acompany, rels_category = self.read_nodes()
        self.create_relationship('Disease', 'Food', rels_recommandeat, 'recommand_eat', '推荐食谱')
        self.create_relationship('Disease', 'Food', rels_noteat, 'no_eat', '忌吃')
        self.create_relationship('Disease', 'Food', rels_doeat, 'do_eat', '宜吃')
        self.create_relationship('Department', 'Department', rels_department, 'belongs_to', '属于')
        self.create_relationship('Disease', 'Drug', rels_commonddrug, 'common_drug', '常用药品')
        self.create_relationship('Producer', 'Drug', rels_drug_producer, 'drugs_of', '生产药品')
        self.create_relationship('Disease', 'Drug', rels_recommanddrug, 'recommand_drug', '好评药品')
        self.create_relationship('Disease', 'Check', rels_check, 'need_check', '诊断检查')
        self.create_relationship('Disease', 'Symptom', rels_symptom, 'has_symptom', '症状')
        self.create_relationship('Disease', 'Disease', rels_acompany, 'acompany_with', '并发症')
        self.create_relationship('Disease', 'Department', rels_category, 'belongs_to', '所属科室')

    '''创建实体关联边（幂等）：MERGE 以「两端节点 + 关系类型」为匹配键，
    关系已存在则匹配不新建（不产生重复边），关系 name 属性仅在新建时写入'''
    def create_relationship(self, start_node, end_node, edges, rel_type, rel_name):
        count = 0
        # 去重处理
        set_edges = []
        for edge in edges:
            set_edges.append('###'.join(edge))
        all = len(set(set_edges))
        # 标签与关系类型为脚本内固定白名单值（非外部输入），以花括号模板拼接；
        # 实体名与关系显示名改为参数化传入（防注入/转义加固）。
        # 幂等机制说明：MERGE 命中「两端节点+关系类型」均已存在的边时直接匹配，
        # 不会重复创建，也不依赖数据库约束拒绝（CREATE 遇约束只会抛异常中断，并非幂等保护）
        query = (
            "match(p:%s),(q:%s) where p.name=$p and q.name=$q "
            "merge (p)-[rel:%s]->(q) on create set rel.name=$rel_name" % (start_node, end_node, rel_type)
        )
        for edge in set(set_edges):
            edge = edge.split('###')
            p = edge[0]
            q = edge[1]
            try:
                self.g.run(query, {"p": p, "q": q, "rel_name": rel_name})
                count += 1
                print(rel_type, count, all)
            except Exception as e:
                # MERGE 语义下重复执行不会产生冲突，走到这里说明是连接异常等真实错误；
                # 记录后跳过该边继续导入（重跑脚本可补齐），不再误报为“幂等保护”
                print(rel_type, 'skip:', p, q, str(e)[:120])
        return

    '''导出数据'''
    def export_data(self):
        Drugs, Foods, Checks, Departments, Producers, Symptoms, Diseases, disease_infos, rels_check, rels_recommandeat, rels_noteat, rels_doeat, rels_department, rels_commonddrug, rels_drug_producer, rels_recommanddrug, rels_symptom, rels_acompany, rels_category = self.read_nodes()
        f_drug = open('drug.txt', 'w+')
        f_food = open('food.txt', 'w+')
        f_check = open('check.txt', 'w+')
        f_department = open('department.txt', 'w+')
        f_producer = open('producer.txt', 'w+')
        f_symptom = open('symptoms.txt', 'w+')
        f_disease = open('disease.txt', 'w+')

        f_drug.write('\n'.join(list(Drugs)))
        f_food.write('\n'.join(list(Foods)))
        f_check.write('\n'.join(list(Checks)))
        f_department.write('\n'.join(list(Departments)))
        f_producer.write('\n'.join(list(Producers)))
        f_symptom.write('\n'.join(list(Symptoms)))
        f_disease.write('\n'.join(list(Diseases)))

        f_drug.close()
        f_food.close()
        f_check.close()
        f_department.close()
        f_producer.close()
        f_symptom.close()
        f_disease.close()

        return



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="医疗知识图谱建图脚本")
    parser.add_argument(
        "--index-only", action="store_true",
        help="只检查/建立索引与唯一约束，不导入数据",
    )
    args = parser.parse_args()

    handler = MedicalGraph()
    # 建图流程开始前先执行幂等的索引/约束检查（失败仅告警不阻断导入）
    try:
        handler.ensure_indexes()
    except Exception as e:
        logger.warning("索引/约束检查失败（不阻断建图）: %s", e)

    if args.index_only:
        print("--index-only：索引/约束检查完成，跳过数据导入")
        sys.exit(0)

    print("step1:导入图谱节点中")
    handler.create_graphnodes()
    print("step2:导入图谱边中")
    handler.create_graphrels()
    

# 指数住院胆囊切除与轻症胆源性急性胰腺炎后90天再住院

本仓库保存论文的公开、安全、仅聚合结果复现材料。研究题目为：

> Completion of Cholecystectomy by Index Discharge and 90-Day Biliary-or-Acute-Pancreatitis Readmission After Coded Mild Biliary Acute Pancreatitis: A Nationwide NRD Study

研究使用2018-2022年HCUP Nationwide Readmissions Database（NRD）。NRD属于受限授权数据库，原始压缩包、逐条住院记录和患者链接变量不在本仓库中，也不得上传。

## 本研究估计什么

主要估计量是NRD设计加权、倾向评分重叠人群中的90天住院再入院绝对风险差：

`risk(A=0) - risk(A=1)`

time zero为指数住院出院日。队列包括主诊断K85.10、出院月份为1-9月、存活出院，并排除了预先规定的重症代理编码。`A=1`表示指数住院记录了胆囊切除（`0FT4*`），`A=0`表示截至出院未记录胆囊切除。主要结局是90天内因胆石病、胆囊炎、胆管炎或急性胰腺炎而发生的主诊断再住院。这个估计量是存活出院患者中“出院时是否完成手术”的关联，不是从入院时开始的治疗策略效果。

冻结后的主要队列为82,948例（A0 34,530；A1 48,418）。校正后的主要风险差为8.700个百分点（95% CI 8.287-9.080，按A0减A1计算）。公开表格遵守HCUP小细胞规则，1-10例统一写作`<11`。

## 两种复现方式

### 公开聚合结果模式

不需要NRD原始文件，直接使用`public_results/`中的聚合CSV/JSON重建6张参考图片：

```bash
python figures/build_public_figures.py --output-dir build/figures
python -m unittest discover -s tests -v
python tests/validate_repository.py
```

脚本会生成Figure 1-6及Supplementary Figure S1-S4各自的PDF、PNG、SVG版本。论文当前使用的精确展示文件保存在`outputs/figures/`；建议将新结果写入其他目录进行比较。

### 授权数据全流程模式

具有有效HCUP/NRD授权的研究者，可在私有计算环境中使用年度Core、Severity、Hospital、CCR文件和本地凭证文件运行完整流程。凭证文件必须位于私有根目录的固定位置：`PRIVATE_ROOT/00_admin/.nrd_credentials.json`；该文件已被Git忽略，绝不能提交。流程默认8线程，并且不会打印凭证内容。

私有根目录中的授权压缩包应按以下路径准备（压缩包内容和密码始终留在私有环境）：

```text
PRIVATE_ROOT/00_admin/.nrd_credentials.json
PRIVATE_ROOT/raw/archives/2018/NRD_2018_CORE.zip
PRIVATE_ROOT/raw/archives/2018/NRD_2018_Severity.zip
PRIVATE_ROOT/raw/archives/2018/NRD_2018_HOSPITAL.zip
PRIVATE_ROOT/raw/archives/2018/cc2018NRD.zip
……
PRIVATE_ROOT/raw/archives/2022/NRD_2022_CORE.zip
PRIVATE_ROOT/raw/archives/2022/NRD_2022_Severity.zip
PRIVATE_ROOT/raw/archives/2022/NRD_2022_HOSPITAL.zip
PRIVATE_ROOT/raw/archives/2022/cc2022NRD.zip
```

启动时，编排脚本会在私有根目录创建所需输出目录，并将`codebook/codebook_v2.0.json`、15个年度布局文件（写入`00_admin/bootstrap/`）、`nrd_build_cohort.py`（写入`03_etl/`）、`bjs_reanalysis.py`（写入`05_models/ate/`）及稳健性分析实现暂存到私有根目录。随后依次构建2018-2022五个年度队列、验证ETL、运行1000次主分析与组件扫描、1000次规范全重拟合bootstrap、1000次校正bootstrap、固定评分稳健性附加分析和最终聚合结果清单。任何受限暂存文件或生成文件都不会复制回本仓库。

```bash
./run_full_pipeline.sh --root /path/to/private/project --workers 8
```

原始压缩包读取器依赖POSIX伪终端，因此完整流程应在Linux服务器或Windows的WSL中运行；原生Windows PowerShell入口会主动拒绝启动。全流程必须与公开聚合模式分离。患者级记录、VisitLink、医院标识、拟合的干扰模型预测和服务器中间文件不得进入本仓库。

## 目录说明

- `analysis/`：队列构建、主要分析、校正bootstrap、稳健性分析和清单脚本；其中`nrd_analysis.py`是授权运行中生成分析就绪表的历史/中间分析脚本。
- `codebook/`：已清理的表型定义、证据表、ontology及2018-2022年Core/Severity/Hospital布局规格。
- `config/`：凭证模板和原始布局模板，不含真实凭证。
- `figures/`：仅聚合结果图片构建脚本。
- `public_results/`：用于当前图片复现的聚合输入和数据字典。
- `outputs/figures/`：论文当前精确图片；`outputs/tables/`：表格和图注参考文件。
- `tests/`：表型单元测试、隐私/完整性测试、数值一致性测试和图片冒烟测试。

## 重要限制

NRD不提供完整门诊随访、诊断级POA指标，也没有单一经过验证的ERCP金标准。因此主要模型使用预先规定的人口学、入院情境、支付/收入/居住地、日历、医院特征和既往90天可观察利用情况；慢性诊断代理变量仅用于敏感性分析。急性倾向的出院编码不进入确认性调整，ERCP结构性编码仅作次要分析。住院时长、收费/成本、出院去向、严重程度字段和治疗后并发症不是基线混杂因素。住院资源和院内伤害结果仅作描述，不能与90天结局拼成同一个因果净获益分数。

`NRD_VisitLink`和`HOSP_NRD`严格限制在同一自然年内使用。指数住院限制在1-9月，是为了保证自然年内有完整90天观察窗口。没有受限NRD环境，无法在公开模式下复现患者级ETL。

## 数据和代码政策

NRD的获取、使用和再分发受HCUP条款约束。本仓库只公开代码、布局元数据、表型定义和聚合结果。请先阅读`DATA_AVAILABILITY.md`、`CODE_AVAILABILITY.md`和`REPRODUCIBILITY.md`。
# 编码轻症胆源性急性胰腺炎指数住院胆囊切除

许可证：MIT。公开仓库：https://github.com/FENG1567/nrd-index-discharge-cholecystectomy

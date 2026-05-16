첫번째 답변>>>>>>

문서만 기준으로 판단하면, 이 설계는 **방향이 맞습니다. 꽤 좋은 편입니다.**
다만 선임개발자에게 보고할 때는 “스키마를 많이 만들었다”가 아니라, **근거 보존형 리서치 그래프를 만들고 있고, 그 핵심 위험은 스키마 수가 아니라 검증·버전관리·평가·쿼리 계약이다**라고 잡아야 합니다.

제 판단을 점수로 말하면 이렇습니다.

| 항목         |                                                                           판단 |
| ---------- | ---------------------------------------------------------------------------: |
| 아키텍처 방향성   |                                                                     8.5 / 10 |
| 온톨로지 레이어링  |                                                                       8 / 10 |
| 증거 추적성 설계  |                                                                       9 / 10 |
| 현재 제품화 준비도 |                                                                   6 ~ 7 / 10 |
| 장기 확장성     |                                                                       8 / 10 |
| 가장 큰 리스크   | schema drift, quote recall, materiality 판단, temporal identity, evaluation 부재 |

가장 강하게 말하고 싶은 건 이겁니다.

> 지금 필요한 건 새 객체 타입을 더 만드는 게 아니라, **객체가 정말 믿을 만한지 검증하는 체계**와 **쿼리/표시/인덱스까지 자동으로 따라오는 schema registry**입니다.

---

# 1. 전체 총평

현재 설명된 KRW Ontology는 단순 RAG보다 훨씬 좋은 방향입니다.

일반 RAG는 보통 이렇게 갑니다.

```text
문서 chunk 검색
→ LLM이 요약
→ 답변
```

이 방식은 빠르지만, 투자 리서치에는 약합니다. 왜냐하면 리서치에서는 “그럴듯한 답변”보다 다음이 더 중요하기 때문입니다.

```text
이 주장의 원문 근거가 어디인가?
그 문구는 실제 SEC filing에 있는가?
그 문구는 risk section의 boilerplate인가, MD&A의 실제 사업 설명인가?
AI가 말한 숫자는 XBRL fact와 맞는가?
작년과 올해 표현이 바뀌었는가?
이 리스크는 새로 생긴 것인가, 반복되는 법적 문구인가?
```

현재 설계는 이 질문들을 정면으로 다룹니다.

특히 좋은 점은 다음입니다.

```text
SourceSpan
→ EvidenceQuote
→ ResearchClaim
→ Semantic Object
→ Edge
→ Company Context
→ MCP Query
```

이 흐름은 **증거 보존형 knowledge graph**에 가깝습니다.
이건 “문서 검색기”가 아니라 “filing-based research memory”입니다.

그리고 투자 리서치 관점에서 이 방향은 맞습니다.
주식 리서치에서 AI가 가장 위험한 순간은 “근거 있는 척하면서 해석을 섞는 순간”인데, 이 구조는 그 위험을 줄이는 데 직접적으로 기여합니다.

---

# 2. 현재 설계에서 가장 잘한 부분

## 2.1 `EvidenceQuote`를 1급 객체로 둔 것

이건 가장 좋은 결정입니다.

많은 RAG 시스템은 chunk를 검색한 뒤 AI가 그 안에서 근거를 “알아서” 가져오게 합니다. 문제는 그러면 원문, 요약, 해석, 추론이 섞입니다.

현재 구조는 다릅니다.

```text
SourceSpan.text 안에서 candidate sentence 생성
→ AI는 candidate ID만 선택
→ 실제 quote_text는 코드가 원문에서 복사
```

이 방식은 매우 좋습니다.

왜냐하면 AI가 quote를 직접 쓰지 않기 때문입니다.
AI가 원문을 재작성하지 않고, 원문에 존재하는 sentence 후보를 고르기만 하므로 hallucination 위험이 크게 줄어듭니다.

이건 선임개발자에게 이렇게 설명하면 됩니다.

> LLM은 원문 생성자가 아니라 selector/classifier로 제한한다. 실제 증거 텍스트는 코드가 원문에서 exact copy한다.

이건 설계 철학이 좋습니다.
투자 리서치용 시스템에서는 이런 제한이 필요합니다.

---

## 2.2 `EvidenceQuote`와 `ResearchClaim`을 분리한 것

이것도 매우 좋은 판단입니다.

`EvidenceQuote`는 원문입니다.

예를 들면:

```text
Our business may be adversely affected by increases in natural gas prices.
```

`ResearchClaim`은 이 원문을 기반으로 만든 최소 해석입니다.

```text
The company has exposure to natural gas price increases through cost channels.
```

이 둘을 분리하지 않으면 문제가 생깁니다.

| 분리하지 않을 경우            | 문제              |
| --------------------- | --------------- |
| quote를 그대로 claim처럼 사용 | 너무 길고 법률 문구가 많음 |
| claim만 저장             | 원문 근거가 사라짐      |
| quote와 claim을 하나로 합침  | 원문과 해석의 경계가 흐려짐 |

현재 구조는 이 경계를 잘 잡았습니다.

제 기준으로는 `ResearchClaim`은 이 시스템의 “semantic compression layer”입니다.
원문을 그대로 쓰기에는 너무 지저분하고, RiskFactor 같은 객체로 바로 가기에는 너무 해석적입니다. 그 중간에 claim을 둔 것은 맞습니다.

---

## 2.3 `ExternalFactorExposure`는 핵심 차별점입니다

이 객체는 진짜 중요합니다.

일반적인 SEC filing RAG는 이런 질문에 약합니다.

```text
Henry Hub 가격이 떨어지면 이 회사에 좋은가?
금리가 올라가면 이 회사의 현금흐름에 어떤 영향이 있나?
LNG 가격이 상승하면 revenue에는 좋지만 feed gas cost에는 나쁜가?
```

이런 질문은 단순 키워드 검색으로 풀 수 없습니다.
회사의 외부요인 노출도를 구조화해야 합니다.

`ExternalFactorExposure`가 있으면 다음처럼 연결할 수 있습니다.

```text
natural_gas_price
→ feed_gas_cost
→ cost_of_revenue
→ gross_margin
→ cash_flow
```

또는:

```text
regulatory_approval
→ project_timing
→ capex
→ revenue_start_date
→ liquidity
```

이건 주식 리서치에서 매우 쓸모 있습니다.
특히 에너지, LNG, 은행, 반도체, 빅테크, 헬스케어처럼 외부 변수 영향이 큰 섹터에서는 핵심 객체입니다.

다만 이 객체는 강력한 만큼 가장 위험하기도 합니다.
뒤에서 말하겠지만 `effect_direction`의 의미를 더 엄격히 정의해야 합니다.

---

## 2.4 File-canonical 설계는 현재 단계에서 맞습니다

SQLite를 원본으로 두지 않고, JSONL/JSON/MD/YAML 파일을 canonical source로 둔 것도 좋은 판단입니다.

초기 research ontology 시스템은 DB-first로 가면 오히려 빨리 망가질 수 있습니다.

파일 기반의 장점은 큽니다.

| 장점            | 의미                                     |
| ------------- | -------------------------------------- |
| Git diff 가능   | AI 산출물이 어떻게 바뀌었는지 검토 가능                |
| 재빌드 가능        | index가 깨져도 artifact에서 다시 생성 가능         |
| 감사 가능         | 객체 하나가 어느 파일에서 왔는지 추적 가능               |
| migration 유연성 | SQLite, Postgres, graph DB로 나중에 옮기기 쉬움 |
| 디버깅 쉬움        | JSONL 한 줄을 바로 확인 가능                    |

이건 초기 시스템에 맞는 선택입니다.

다만 장기적으로는 “파일이 원본”이라는 원칙을 유지하더라도, **run manifest, schema version, taxonomy version, model version**을 반드시 같이 저장해야 합니다.

---

## 2.5 AI를 좁게 쓰는 방향도 맞습니다

현재 설계는 AI에게 모든 걸 맡기지 않습니다.

좋은 구조는 이겁니다.

```text
AI:
- quote candidate 선택
- claim extraction
- classification hint

Code:
- exact quote copy
- reference validation
- edge projection
- metric calculation
- object grouping
- index building
```

이 방향이 맞습니다.

투자 리서치 시스템에서 AI에게 너무 많은 권한을 주면, 결과는 화려하지만 재현성이 무너집니다.

특히 edge를 AI가 직접 생성하지 않고 reference field에서 deterministic projection하는 것은 좋은 선택입니다.

```text
supported_by_quotes
→ supports edge 자동 생성
```

이런 방식이 안정적입니다.

---

# 3. 지금 설계에서 가장 큰 위험

좋은 설계지만, 아직 위험한 지점도 분명합니다.
제가 보기에는 핵심 리스크가 8개 있습니다.

---

## 3.1 “근거가 있다”와 “중요하다”는 다릅니다

이건 제일 중요합니다.

SEC 10-K/10-Q에는 boilerplate risk가 많습니다.
특히 Item 1A risk factors는 법무팀이 방어적으로 쓰는 문구가 많습니다.

예를 들어 이런 문장이 있다고 합시다.

```text
Cybersecurity incidents could adversely affect our business.
```

이 문장은 실제 filing에 있고, quote exact match도 됩니다.
그렇다고 이 회사의 핵심 리스크라고 말할 수는 없습니다.

즉:

```text
EvidenceQuote 존재
≠
투자적으로 중요한 리스크
```

현재 구조는 quote 추적성은 강하지만, **materiality ranking**은 아직 별도 문제입니다.

따라서 `RiskFactor`, `Headwind`, `ExternalFactorExposure`에는 단순 confidence만으로 부족합니다.

추가로 이런 필드 또는 scoring이 필요합니다.

| 필드                       | 의미                                  |
| ------------------------ | ----------------------------------- |
| `specificity_score`      | 회사/사업/프로젝트/숫자에 구체적으로 연결되는 정도        |
| `boilerplate_score`      | 일반 법률 문구일 가능성                       |
| `section_weight`         | Item 1A, MD&A, Notes 등 section별 중요도 |
| `recurrence_status`      | 매년 반복되는 문구인지, 새로 등장했는지              |
| `metric_link_strength`   | 실제 재무 metric과 연결되는 정도               |
| `business_link_strength` | 주요 사업활동과 연결되는 정도                    |
| `new_or_changed`         | 전기 대비 새로 생겼거나 표현이 강해졌는지             |

강하게 말하면, 이 시스템의 다음 퀄리티 점프는 schema 추가가 아니라 **materiality와 specificity 평가**에서 나옵니다.

---

## 3.2 `confidence`의 의미가 애매해질 수 있습니다

현재 여러 객체에 `confidence`가 있습니다.
이 필드는 그대로 두면 위험합니다.

왜냐하면 confidence가 무엇을 의미하는지 불명확해질 수 있기 때문입니다.

| confidence 종류             | 의미                            |
| ------------------------- | ----------------------------- |
| extraction confidence     | AI가 이 객체를 제대로 추출했을 가능성        |
| evidence strength         | 원문 근거가 얼마나 직접적인지              |
| materiality confidence    | 이 이슈가 투자적으로 중요할 가능성           |
| classification confidence | category/factor label이 맞을 가능성 |
| ranking confidence        | 검색 결과 상단에 둘 만한지               |

이걸 하나의 `confidence`로 뭉치면 나중에 해석이 꼬입니다.

추천은 다음처럼 분리하는 것입니다.

```text
extraction_confidence
evidence_strength
classification_confidence
materiality_hint
materiality_basis
ranking_score
```

특히 `materiality_hint`는 반드시 `materiality_basis`와 같이 있어야 합니다.

예를 들면:

```json
{
  "materiality_hint": "high",
  "materiality_basis": [
    "mentions revenue",
    "linked to primary business activity",
    "changed from prior 10-Q",
    "supported by MD&A quote"
  ]
}
```

이렇게 해야 “왜 중요하다고 봤는지” 설명할 수 있습니다.

---

## 3.3 `ExternalFactorExposure.effect_direction`은 지금 형태로는 모호합니다

현재 `effect_direction`이 positive, negative, mixed, uncertain으로 되어 있습니다.

이건 직관적이지만, 시나리오 분석에서는 위험합니다.

예를 들어:

```text
factor = natural_gas_price
impact_channel = cost_of_revenue
effect_direction = negative
```

여기서 negative가 무슨 뜻일까요?

1. 천연가스 가격 상승이 회사에 negative라는 뜻인가?
2. 천연가스 가격 하락이 cost_of_revenue에 negative라는 뜻인가?
3. cost_of_revenue가 증가한다는 뜻인가?
4. margin에 부정적이라는 뜻인가?

방향성이 애매합니다.

`ExternalFactorExposure`는 반드시 다음 구조로 바꾸는 게 좋습니다.

```json
{
  "factor": "natural_gas_price",
  "impact_channel": "cost_of_revenue",
  "metric_effect_when_factor_increases": "increases",
  "company_effect_when_factor_increases": "negative",
  "metric_effect_when_factor_decreases": "decreases",
  "company_effect_when_factor_decreases": "positive",
  "pass_through": "partial",
  "offsets": ["long-term contracts", "hedging", "fixed-fee structure"],
  "evidence_grade": "direct"
}
```

이렇게 해야 이런 질문을 안정적으로 처리할 수 있습니다.

```text
Henry Hub가 내려가면 VG에 좋은가?
```

답변 흐름은 다음처럼 됩니다.

```text
natural_gas_price decreases
→ feed gas cost likely decreases
→ cost_of_revenue decreases
→ margin/cash flow may improve
→ 단, 계약 구조와 pass-through 여부 확인 필요
```

현재 `effect_direction`만 있으면 이 방향 추론이 흔들릴 수 있습니다.

제 의견은 명확합니다.

> `ExternalFactorExposure`는 이 시스템의 핵심 객체이므로, factor 변화 방향과 metric 변화 방향을 반드시 분리해야 합니다.

---

## 3.4 quote candidate recall이 병목입니다

현재 방식은 exactness에 강합니다.

```text
SourceSpan.text에서 sentence 후보 생성
→ AI가 후보 ID 선택
→ 코드가 quote 복사
```

이건 quote hallucination을 줄입니다.

하지만 치명적인 tradeoff가 있습니다.

> 후보 생성 단계에서 중요한 문장이 빠지면, 이후 단계에서는 절대 복구할 수 없습니다.

따라서 품질 개선의 핵심은 prompt가 아니라 candidate generation입니다.

개선해야 할 부분은 다음입니다.

| 영역                       | 개선 방향                                                          |
| ------------------------ | -------------------------------------------------------------- |
| sentence splitting       | SEC filing의 긴 문장, bullet, footnote, table row 처리               |
| section-aware extraction | Item 1A, Item 7, Notes, Business section별 candidate policy 차등화 |
| table text handling      | 숫자 table 주변 문장과 row를 candidate로 만들기                            |
| paragraph context        | 단일 sentence만으로 의미가 부족한 경우 preceding/following sentence 포함      |
| duplicate handling       | risk factor 반복 문구 dedup                                        |
| candidate recall test    | 사람이 뽑은 gold quote가 candidate에 들어왔는지 측정                         |

여기서 중요한 metric은 이것입니다.

```text
candidate_recall = 사람이 중요하다고 본 quote 중 candidate set에 포함된 비율
```

이 값이 낮으면 뒤의 AI extraction은 아무리 좋아도 무너집니다.

---

## 3.5 Pydantic과 YAML schema의 권위가 애매합니다

현재 설명상 Pydantic model과 YAML schema가 둘 다 있습니다.

이건 흔한 문제입니다.
처음에는 괜찮지만, 나중에는 반드시 drift가 납니다.

예를 들면:

```text
Pydantic에는 field가 추가됨
YAML에는 누락됨

또는

YAML에는 object type이 추가됨
index builder는 모름
```

이런 일이 생깁니다.

제 추천은 강합니다.

> Pydantic을 runtime schema source of truth로 두고, YAML은 registry/config/documentation 역할로 제한하는 게 좋습니다.

즉:

```text
Pydantic:
- field 정의
- validation
- JSON Schema generation

YAML:
- object type registry
- display config
- text index field config
- relation projection config
- taxonomy config
- extraction policy
```

반대로 YAML을 source of truth로 두고 Pydantic을 생성해도 됩니다.
하지만 둘을 사람이 따로 관리하면 안 됩니다.

선임개발자가 이 부분을 물어볼 가능성이 높습니다.

보고할 때는 이렇게 말하는 게 좋습니다.

> 현재는 Pydantic이 실행 기준 source of truth이고, YAML은 schema contract와 확장 설정에 가깝다. 다만 drift 방지를 위해 JSON Schema generation 또는 registry 기반 자동화를 넣을 계획이다.

---

## 3.6 객체 추가 시 pipeline 전체를 바꿔야 하는 문제

문서에서 이미 잘 짚었습니다.

새 object type을 추가하면 단순히 Pydantic model만 추가하는 게 아닙니다.

반드시 같이 바뀌어야 합니다.

```text
Pydantic model
YAML registry
relations.yaml
validators
artifact writer
index builder
FTS text fields
MCP response compact renderer
trace renderer
display contract
skill docs
evaluation cases
```

이 비용이 큽니다.

따라서 새 schema를 자주 추가하면 시스템이 빨리 복잡해집니다.

여기서 필요한 건 `OntologyRegistry`입니다.

예를 들면 이런 구조입니다.

```yaml
RiskFactor:
  artifact_file: risk_factors.jsonl
  text_fields:
    - name
    - description
    - category
  reference_fields:
    supported_by_claims: ResearchClaim
    supported_by_quotes: EvidenceQuote
  default_relations:
    supported_by_claims: describes_risk
    supported_by_quotes: directly_supported_by
  compact_display:
    title: name
    subtitle: category
    body: description
  trace_fields:
    - supported_by_claims
    - supported_by_quotes
```

이런 registry가 있으면 index builder, validator, MCP compact response가 같은 설정을 공유할 수 있습니다.

결론은 이겁니다.

> 새 객체 타입 추가보다 먼저 object registry를 만들어야 합니다.

---

## 3.7 Temporal identity가 생각보다 어렵습니다

`TemporalLink`, `TrendObservation`, `ChangeEvent`는 좋은 방향입니다.
하지만 이 레이어는 꽤 어렵습니다.

예를 들어 다음 표현들이 모두 같은 프로젝트를 가리킬 수 있습니다.

```text
CP2
CP2 LNG
CP2 LNG project
the CP2 Project
Venture Global's CP2 export facility
```

이걸 단순 문자열로 매칭하면 TemporalLink 품질이 낮아집니다.

따라서 장기적으로는 `CanonicalEntity` 또는 `EntityMention` 계층이 필요할 가능성이 큽니다.

예시는 다음입니다.

```json
{
  "id": "entity:project:VG:CP2",
  "entity_type": "project",
  "canonical_name": "CP2 LNG",
  "aliases": ["CP2", "CP2 Project", "CP2 LNG project"],
  "ticker": "VG"
}
```

그리고 각 quote나 claim이 이 entity를 참조할 수 있어야 합니다.

```json
{
  "claim_text": "The company expects CP2 to begin operations in late 2029.",
  "entities": ["entity:project:VG:CP2"]
}
```

이게 없으면 multi-period 비교가 흔들립니다.

특히 다음 질문들이 어려워집니다.

```text
CP2 관련 리스크가 작년 대비 바뀌었나?
이 프로젝트의 COD 타임라인이 바뀌었나?
새로 등장한 regulatory risk가 특정 프로젝트와 연결되나?
```

따라서 저는 `CanonicalEntity` 계층을 장기적으로 꽤 중요하게 봅니다.
다만 이걸 바로 큰 schema로 만들기보다는, 처음에는 entity registry 또는 alias map으로 시작해도 됩니다.

---

## 3.8 숫자 레이어는 unit/dimension/context가 핵심입니다

숫자 객체를 AI에게 맡기지 않는 방향은 맞습니다.

하지만 숫자 레이어에서 중요한 것은 단순 value가 아닙니다.

반드시 다음이 필요합니다.

| 필드                     | 이유                             |
| ---------------------- | ------------------------------ |
| `unit`                 | USD, shares, %, MTPA 등         |
| `scale`                | ones, thousands, millions      |
| `period_type`          | instant, duration              |
| `fiscal_period`        | FY2025, Q1 2026                |
| `xbrl_context_ref`     | 원본 XBRL context 추적             |
| `segment_dimension`    | AWS, Services, LNG segment 등   |
| `geography_dimension`  | US, International 등            |
| `normalization_method` | adjusted, reported, derived 여부 |
| `calculation_formula`  | DerivedMetricValue 계산 근거       |
| `input_object_ids`     | 계산에 들어간 metric IDs             |

숫자에서 가장 흔한 오류는 hallucination이 아니라 **비교 불가능한 숫자를 비교하는 것**입니다.

예를 들면:

```text
FY revenue vs quarterly revenue
reported margin vs adjusted margin
company-level revenue vs segment revenue
USD millions vs USD thousands
```

따라서 숫자 객체는 값보다 context가 더 중요합니다.

---

# 4. 객체별 구체적 판단

## 4.1 `SourceDocument`

좋습니다.
다만 SEC filing 기준이라면 `accession_number`는 거의 필수입니다.

추천 필드:

```text
accession_number
cik
ticker
company_name
document_type
filing_date
period_end_date
fiscal_year
fiscal_quarter
source_url
raw_text_hash
clean_text_hash
metadata_ref
schema_version
```

특히 `filing_date`와 `period_end_date`는 분리해야 합니다.

예를 들어 2025년 10-K라 해도 filing date는 2026년일 수 있습니다.
시계열 분석에서 이 둘을 섞으면 안 됩니다.

---

## 4.2 `SourceSpan`

좋습니다.
다만 `start_char`, `end_char`는 clean.md가 바뀌면 깨질 수 있습니다.

그래서 다음을 같이 두는 게 좋습니다.

```text
source_document_id
section_name
section_path
span_index
start_char
end_char
text_hash
clean_text_hash_at_extraction
extraction_run_id
```

그리고 가능하면 span은 sentence 단위보다 조금 큰 paragraph/chunk 단위로 유지하고, quote는 sentence 또는 clause 단위로 뽑는 것이 좋습니다.

```text
SourceSpan = context container
EvidenceQuote = precise evidence
```

이 역할 구분이 중요합니다.

---

## 4.3 `EvidenceQuote`

현재 구조의 핵심입니다.
좋습니다.

다만 추가하면 좋은 필드가 있습니다.

```text
quote_hash
candidate_id
candidate_generation_method
quote_exact_match_verified
quote_context_before
quote_context_after
boilerplate_score
specificity_score
```

특히 `quote_exact_match_verified`는 validator에서 반드시 true여야 합니다.

```text
EvidenceQuote.quote_text ∈ SourceSpan.text
```

이건 production에서 100%여야 합니다.
99%도 부족합니다.

---

## 4.4 `LanguageSignal`

괜찮습니다.
하지만 단순 signal보다 다음 축을 분리하는 것이 좋습니다.

| 축           | 예                                           |
| ----------- | ------------------------------------------- |
| polarity    | positive, negative, neutral                 |
| modality    | actual, potential, conditional, obligation  |
| temporality | historical, current, future                 |
| certainty   | certain, likely, possible, uncertain        |
| legal tone  | cautionary, contractual, regulatory         |
| mitigation  | mitigated, unmitigated, partially mitigated |

예를 들어:

```text
may adversely affect
```

는 다음처럼 해석될 수 있습니다.

```json
{
  "polarity": "negative",
  "modality": "potential",
  "temporality": "future_or_potential",
  "certainty": "possible"
}
```

이렇게 구조화하면 나중에 검색과 ranking이 좋아집니다.

---

## 4.5 `ResearchClaim`

이 객체는 매우 중요합니다.
다만 atomicity를 강하게 관리해야 합니다.

나쁜 claim:

```text
The company faces regulatory, commodity price, and liquidity risks that may affect revenue and margins.
```

이건 너무 큽니다.

좋은 claim:

```text
The company is exposed to natural gas price changes through feed gas procurement costs.
```

또는:

```text
Regulatory approval delays may affect the timing of the CP2 project.
```

`ResearchClaim`에는 다음 필드를 추가하는 것이 좋습니다.

```text
claim_subject
claim_predicate
claim_object
claim_scope
claim_period
claim_directness
support_level
requires_inference
```

특히 중요한 건 이겁니다.

```text
requires_inference: true / false
```

예를 들어 원문이 직접 말한 것과, 원문을 바탕으로 시스템이 한 단계 추론한 것은 분리해야 합니다.

답변에서도 이 차이를 표시해야 합니다.

```text
Filing directly states X.
The system infers Y from X.
```

이 차이가 없으면 결국 RAG식 해석 혼합 문제가 다시 생깁니다.

---

## 4.6 `RiskFactor`, `GrowthDriver`, `Headwind`

이 세 객체는 실용적으로 좋습니다.
다만 구조가 거의 같다면 내부적으로는 base type을 공유하는 것이 좋습니다.

예를 들면:

```text
BusinessFactorBase
- name
- category
- description
- supported_by_claims
- supported_by_quotes
- affects
- qualitative_impact
- confidence
- materiality_hint
- materiality_basis
- specificity_score
```

그리고 외부 product/API에서는 다음처럼 보여줄 수 있습니다.

```text
RiskFactor
GrowthDriver
Headwind
```

즉, 내부 구현은 공통화하고, 사용자-facing type은 유지하는 방식이 좋습니다.

또 하나 주의할 점은 `RiskFactor`와 `Headwind`가 겹칠 수 있다는 점입니다.

예를 들어:

```text
higher feed gas costs
```

는 현재 발생 중이면 `Headwind`, 미래 가능성이면 `RiskFactor`입니다.

따라서 둘의 차이를 이렇게 명확히 해야 합니다.

| 객체           | 기준                                |
| ------------ | --------------------------------- |
| RiskFactor   | 잠재적 손실 가능성, future/conditional 중심 |
| Headwind     | 현재 또는 최근 실적에 부담을 준 요인             |
| GrowthDriver | 성장, 수요, 매출, 마진 개선 동인              |

이 기준이 없으면 object duplication이 생깁니다.

---

## 4.7 `BusinessActivity`

좋습니다.
이 객체는 business model 질문에 필수입니다.

다만 `BusinessActivity`는 company profile의 backbone 역할을 해야 합니다.

예를 들어 LNG 회사라면:

```text
LNG sales
feed gas procurement
liquefaction operations
project development
long-term offtake contracts
shipping/logistics
```

이 활동들이 profile에 안정적으로 잡혀야 이후 exposure, risk, metric 연결이 좋아집니다.

추천 필드:

```text
activity_type
activity_role
revenue_relevance
cost_relevance
capex_relevance
working_capital_relevance
related_segments
related_projects
related_external_factors
related_metrics
```

현재 `revenue_relevance`, `cost_relevance`가 있는 건 좋습니다.
여기에 `capex_relevance`도 넣는 게 좋습니다. 프로젝트 중심 회사에서는 매우 중요합니다.

---

## 4.8 `ExternalFactorExposure`

가장 중요한 개선 대상입니다.

현재 이 객체는 방향이 맞지만, 더 엄격해야 합니다.

추천 구조는 다음입니다.

```json
{
  "factor": "natural_gas_price",
  "factor_category": "commodity",
  "benchmark": "Henry Hub",
  "exposure_channel": "input_cost",
  "impact_channel": "cost_of_revenue",
  "metric_effect_when_factor_increases": "increases",
  "company_effect_when_factor_increases": "negative",
  "metric_effect_when_factor_decreases": "decreases",
  "company_effect_when_factor_decreases": "positive",
  "pass_through_mechanism": "partial_or_unknown",
  "offsetting_factors": ["fixed-fee contracts", "hedging", "contractual pass-through"],
  "evidence_grade": "direct",
  "materiality": "medium",
  "time_horizon": "ongoing"
}
```

이렇게 해야 factor scenario query가 안정화됩니다.

또 하나 중요한 점은 `benchmark`입니다.
LNG/energy에서는 benchmark가 중요합니다.

```text
Henry Hub
JKM
TTF
Brent
WTI
```

이걸 taxonomy로 잡아두면 외부 market premise와 연결하기 좋아집니다.

---

## 4.9 `AssumptionCandidate`

좋은 객체입니다.
다만 위험합니다.

왜냐하면 사용자가 이것을 “추천 가정값”으로 오해할 수 있기 때문입니다.

따라서 `AssumptionCandidate`는 반드시 “valuation input candidate”이지 “valuation conclusion”이 아니어야 합니다.

추천 필드:

```text
assumption_type
assumption_text
value_hint
value_range_low
value_range_high
unit
source_period
basis_type
calculation_method
supported_by_claims
supported_by_quotes
supported_by_metrics
caveats
applicability_conditions
```

특히 `basis_type`이 중요합니다.

예:

```text
company_disclosed
historical_reported
derived_from_metric
management_expectation
inferred_from_project_timing
```

이렇게 해야 downstream valuation engine이 이 가정을 어떻게 다뤄야 하는지 알 수 있습니다.

---

## 4.10 `CompanyBusinessProfile`

좋습니다.
하지만 이 객체는 최종 답변 근거라기보다 **query planning map**입니다.

즉, profile은 다음에 쓰여야 합니다.

```text
ticker만 있는 broad question
→ CompanyBusinessProfile 확인
→ 주요 activity/factor/metric vocabulary 생성
→ 관련 object 검색
→ quote/claim으로 trace
→ 답변
```

중요한 점은 profile이 너무 요약적이면 안 된다는 것입니다.

`CompanyBusinessProfile`에는 다음이 있으면 좋습니다.

```text
primary_business_activities
primary_revenue_sources
primary_cost_sources
major_projects
key_external_factors
key_metrics
key_risks
key_growth_drivers
query_vocabulary
korean_aliases
english_aliases
```

특히 사용자가 한국어로 질문한다면 `korean_aliases` 또는 bilingual topic map이 매우 중요합니다.

예:

```text
천연가스 가격
→ natural_gas_price
→ Henry Hub
→ feed_gas_cost
→ cost_of_revenue
```

이 연결이 좋아야 MCP 검색 품질이 올라갑니다.

---

# 5. 새 객체 추가에 대한 판단

문서의 결론에 동의합니다.

> 지금 당장 무작정 새 객체 타입을 늘리면 안 됩니다.

다만 저는 우선순위를 조금 더 명확히 잡겠습니다.

---

## 5.1 지금 당장 추가하면 좋은 것은 business object가 아니라 meta/schema object입니다

새로운 business object보다 먼저 필요한 것은 다음입니다.

### 1. `ExtractionRun` 또는 `RunManifest`

이건 거의 필수입니다.

```json
{
  "run_id": "run:2026-05-16:VG:FY2025:001",
  "source_document_id": "source:VG:FY2025:10K",
  "schema_version": "1.3.0",
  "pipeline_version": "0.8.2",
  "taxonomy_version": "energy_lng:0.4.0",
  "model_name": "gpt-...",
  "created_at": "2026-05-16T..."
}
```

이게 없으면 나중에 이런 질문에 답하기 어렵습니다.

```text
왜 지난번과 추출 결과가 달라졌나?
이 quote는 어떤 schema version에서 나온 건가?
taxonomy를 바꾼 뒤 어떤 객체가 달라졌나?
```

---

### 2. `OntologyRegistry`

이건 객체라기보다는 schema registry/config입니다.

역할은 다음입니다.

```text
object type별 artifact file
text index field
reference field
relation projection rule
compact display rule
trace rule
validation rule
```

이걸 만들어야 object 추가 비용이 줄어듭니다.

---

### 3. `CanonicalEntity` 또는 entity alias registry

이건 장기적으로 매우 유용합니다.

특히 다음을 추적하려면 필요합니다.

```text
projects
segments
products
contracts
counterparties
regulators
facilities
geographies
```

예:

```json
{
  "id": "entity:project:VG:CP2",
  "entity_type": "project",
  "canonical_name": "CP2 LNG",
  "aliases": ["CP2", "CP2 Project", "CP2 LNG project"]
}
```

이걸 넣으면 `ProjectMilestone`, `RegulatoryProceeding`, `ContractTerm`의 품질이 훨씬 좋아집니다.

---

## 5.2 business object 추가 우선순위

문서에 나온 후보들에 대해 제 판단은 이렇습니다.

| 후보                     | 제 판단                                     |  우선순위 |
| ---------------------- | ---------------------------------------- | ----: |
| `ProjectMilestone`     | 인프라, LNG, 반도체, 에너지에서는 매우 유용              |    높음 |
| `ContractTerm`         | LNG SPA, debt, lease, take-or-pay 분석에 중요 |    높음 |
| `RegulatoryProceeding` | 승인/소송/규제 중심 회사에서는 중요                     |    중상 |
| `CapitalStructureItem` | debt maturity, liquidity 질문이 많으면 필요      |    중상 |
| `GuidanceItem`         | SEC filing만 볼 때는 제한적, call/deck 확장 시 중요  |    중간 |
| `SegmentPerformance`   | 바로 객체화하기보다 metric dimension으로 먼저 처리      | 중간 이하 |

제 의견은 다음과 같습니다.

## 5.3 `ProjectMilestone`은 꽤 빨리 추가할 가치가 있습니다

LNG, infrastructure, semiconductor fab, biotech pipeline 같은 회사에서는 프로젝트 타임라인이 투자 thesis의 핵심입니다.

예를 들어:

```text
FID
financial close
construction start
phase completion
COD
commercial operations
capacity ramp
regulatory approval
```

이런 건 `ResearchClaim`만으로는 UI와 timeline query가 불편합니다.

따라서 프로젝트 중심 회사를 많이 다룬다면 `ProjectMilestone`은 빠르게 추가할 만합니다.

추천 필드:

```text
project_entity_id
project_name
milestone_type
status
target_date_or_period
actual_date
date_confidence
capacity
capex
dependency
delay_reason
supported_by_claims
supported_by_quotes
```

특히 `date_confidence`가 필요합니다.

```text
expected in late 2029
targeted for 2029
could begin in 2029
completed in March 2026
```

이 표현들은 확정성이 다릅니다.

---

## 5.4 `ContractTerm`도 도메인에 따라 매우 중요합니다

LNG 회사, debt-heavy 회사, lease-heavy 회사, subscription 계약이 중요한 회사는 계약 조건이 핵심입니다.

`ContractTerm`은 다음 질문에 유용합니다.

```text
이 회사 매출은 fixed fee인가 commodity exposed인가?
take-or-pay 계약인가?
계약 기간이 얼마나 남았나?
counterparty concentration이 있는가?
debt covenant 리스크가 있는가?
```

추천 필드:

```text
contract_entity_id
contract_name
counterparty_entity_id
contract_type
term_start
term_end
pricing_mechanism
volume_commitment
minimum_commitment
termination_condition
renewal_condition
revenue_or_cost_relevance
supported_by_claims
supported_by_quotes
```

다만 계약 추출은 어렵습니다.
원문에서 조건이 여러 문단과 표에 흩어져 있기 때문입니다.

따라서 처음에는 `ContractTerm`을 완전 자동화하기보다, high-confidence quote 기반으로 제한하는 게 좋습니다.

---

## 5.5 `SegmentPerformance`는 바로 객체화하지 않는 게 낫습니다

이건 문서의 후보 중에서 제가 가장 조심스럽게 보는 객체입니다.

segment revenue, segment operating income, segment margin은 중요합니다.
하지만 이걸 별도 객체로 만들기 전에 먼저 `FinancialMetricValue`에 dimension을 추가하는 게 낫습니다.

예:

```json
{
  "metric_name": "revenue",
  "value": 1000000000,
  "unit": "USD",
  "period": "FY2025",
  "dimensions": {
    "segment": "AWS",
    "geography": null,
    "product": null
  }
}
```

이렇게 하면 segment metric을 처리할 수 있습니다.

`SegmentPerformance` 객체는 다음 조건이 충족될 때 추가하면 됩니다.

```text
segment별 narrative analysis가 자주 필요하다
segment margin bridge를 UI로 보여줘야 한다
segment별 risk/driver/headwind를 묶어야 한다
```

그 전에는 metric dimension으로 충분합니다.

---

## 5.6 `GuidanceItem`은 source 확장 후에 추가하는 게 맞습니다

10-K/10-Q에도 guidance가 있을 수 있지만, 보통 guidance는 earnings call, press release, investor presentation에서 더 많이 나옵니다.

따라서 현재 시스템이 SEC filing ontology라면 `GuidanceItem`은 급하지 않습니다.

다만 향후 source가 확장되면 중요해집니다.

```text
SEC filing
earnings call transcript
investor presentation
press release
```

이 단계가 오면 `GuidanceItem`은 반드시 필요해질 가능성이 큽니다.

---

# 6. 지금 가장 먼저 발전시켜야 할 방향

제 기준으로 우선순위는 이렇습니다.

```text
1. Schema registry / source of truth 정리
2. Validation과 evaluation 강화
3. Candidate generation recall 개선
4. Materiality / specificity / boilerplate scoring
5. Temporal identity와 change detection 강화
6. Taxonomy / sector pack / metric dictionary 확장
7. 그 다음 business object 선택적 추가
```

---

## 6.1 1순위: Schema registry를 만들어야 합니다

현재는 object type을 추가할 때 여러 곳을 수동으로 바꿔야 합니다.

이건 장기적으로 위험합니다.

해결책은 `OntologyRegistry`입니다.

각 object type에 대해 다음을 하나의 registry에 정의해야 합니다.

```text
artifact file
text fields
filterable fields
reference fields
relation projection
required validators
default compact renderer
trace renderer
MCP response shape
```

이렇게 되면 새 object type 추가가 훨씬 안전해집니다.

예:

```yaml
ExternalFactorExposure:
  artifact_file: external_factor_exposures.jsonl
  text_fields:
    - factor
    - benchmark
    - mechanism
    - impact_channel
  filter_fields:
    - ticker
    - period
    - factor
    - factor_category
    - impact_channel
    - materiality
  reference_fields:
    supported_by_claims: ResearchClaim
    supported_by_quotes: EvidenceQuote
    related_business_activities: BusinessActivity
    related_metrics: FinancialMetricValue
  relation_projection:
    supported_by_claims: describes_exposure
    related_business_activities: affects_activity
  compact_display:
    title: factor
    subtitle: impact_channel
    body: mechanism
```

이 registry를 기준으로 다음을 자동화할 수 있습니다.

```text
index builder
validator
MCP compact response
trace response
display block
```

이게 들어가면 시스템이 훨씬 견고해집니다.

---

## 6.2 2순위: Evaluation harness가 필요합니다

지금 구조는 좋아 보이지만, 실제 품질은 평가 없이는 알 수 없습니다.

필수 평가 항목은 다음입니다.

| 평가 항목                       | 의미                                   |
| --------------------------- | ------------------------------------ |
| quote exact match rate      | quote가 span 안에 실제 존재하는 비율            |
| candidate recall            | gold quote가 후보에 포함되는 비율              |
| claim support precision     | claim이 quote로 정말 뒷받침되는 비율            |
| unsupported claim rate      | 근거 없는 claim 비율                       |
| invalid reference rate      | 깨진 object reference 비율               |
| duplicate object rate       | 같은 의미 객체가 중복 생성되는 비율                 |
| factor mapping accuracy     | external factor canonicalization 정확도 |
| temporal link accuracy      | 기간 간 같은 객체 연결 정확도                    |
| numeric validation accuracy | AI claim 숫자와 XBRL/metric 일치 여부       |
| answer trace success rate   | 최종 답변 claim이 quote까지 trace되는 비율      |

이 중 production quality gate로는 최소 다음이 필요합니다.

```text
quote exact match rate = 100%
invalid reference rate = 0%
schema validation pass = 100%
unsupported claim rate = 매우 낮게
candidate recall = 지속 측정
```

특히 `quote exact match rate`와 `invalid reference rate`는 타협하면 안 됩니다.

---

## 6.3 3순위: Materiality ranking을 만들어야 합니다

검색 결과가 많아지면 단순 FTS로는 부족합니다.

예를 들어 “AAPL margin risk”를 물었을 때, 시스템은 수십 개 quote와 claim을 찾을 수 있습니다.
그중 어떤 것을 위에 올릴지가 중요합니다.

추천 ranking signal:

```text
direct evidence grade
section weight
specificity score
boilerplate score inverse
metric linkage strength
business activity linkage strength
recency
changed from prior period
materiality hint
number present
management discussion section bonus
risk factor generic penalty
```

예시 ranking formula는 이런 식입니다.

```text
rank_score =
  direct_evidence_weight
+ specificity_score
+ metric_link_score
+ business_activity_link_score
+ temporal_change_score
+ section_weight
- boilerplate_penalty
```

이건 답변 품질을 크게 올립니다.

SEC risk factor는 generic phrase가 많기 때문에 `boilerplate_penalty`는 꼭 필요합니다.

---

## 6.4 4순위: Temporal diff를 강화해야 합니다

multi-period 질문은 이 시스템의 강점이 될 수 있습니다.

예:

```text
최근 10-Q에서 새로 생긴 리스크는?
전년 10-K 대비 margin headwind 표현이 강해졌나?
CP2 일정이 바뀌었나?
회사가 언급하는 주요 외부요인이 달라졌나?
```

이걸 잘하려면 단순히 object를 period별로 저장하는 것만으로는 부족합니다.

필요한 필드:

```text
first_seen_period
last_seen_period
appeared_in_current_period
removed_in_current_period
changed_from_prior_period
change_type
change_summary
prior_object_id
current_object_id
identity_confidence
```

`ChangeEvent`도 더 구체화할 수 있습니다.

```text
change_type:
- newly_disclosed
- removed
- wording_strengthened
- wording_softened
- metric_increased
- metric_decreased
- target_date_delayed
- target_date_accelerated
- status_changed
```

이런 구조가 있어야 “변화”를 안정적으로 말할 수 있습니다.

---

## 6.5 5순위: Taxonomy와 sector pack 확장

문서에서 말한 대로 schema 추가보다 taxonomy 확장이 우선입니다.

특히 투자 리서치에서는 sector별 vocabulary가 매우 중요합니다.

예를 들어 “마진 압박”이라는 한국어 질문 하나도 섹터마다 다릅니다.

| 섹터   | 마진 압박 요인                                                        |
| ---- | --------------------------------------------------------------- |
| LNG  | feed gas cost, liquefaction cost, shipping, contract spread     |
| 반도체  | utilization, wafer cost, ASP, memory cycle                      |
| 빅테크  | AI capex, depreciation, traffic acquisition cost, cloud pricing |
| 은행   | deposit beta, funding cost, credit loss                         |
| 소비재  | promotion, volume deleverage, commodity input cost              |
| SaaS | sales efficiency, hosting cost, churn, RPO conversion           |

따라서 sector pack에는 다음이 있어야 합니다.

```text
canonical business activities
canonical revenue sources
canonical cost sources
canonical external factors
canonical metrics
aliases
Korean synonyms
English synonyms
section search hints
object grouping rules
```

특히 한국어 사용자를 고려하면 bilingual alias가 중요합니다.

예:

```yaml
natural_gas_price:
  aliases:
    en:
      - natural gas price
      - Henry Hub
      - feed gas price
    ko:
      - 천연가스 가격
      - 헨리허브
      - 가스 가격
      - 원료가스 가격
```

이게 있어야 한국어 질문을 canonical factor로 안정적으로 매핑할 수 있습니다.

---

# 7. 선임개발자가 물어볼 만한 질문과 답변 방향

## 질문 1. “이거 그냥 RAG보다 왜 나은가?”

답변 방향:

```text
일반 RAG는 retrieved chunk와 LLM 해석이 섞입니다.
이 시스템은 EvidenceQuote를 1급 객체로 만들고, 모든 ResearchClaim과 semantic object가 quote까지 trace됩니다.
따라서 답변 후에도 원문 문구, section, filing, period까지 역추적할 수 있습니다.
```

핵심 문장:

> 목적은 답변 생성이 아니라, 근거 추적 가능한 리서치 그래프 구축입니다.

---

## 질문 2. “왜 DB-first가 아닌가?”

답변 방향:

```text
초기에는 AI 산출물의 diff, 감사, 재빌드 가능성이 중요합니다.
JSONL/JSON/MD/YAML 파일을 canonical artifact로 두고, SQLite는 조회용 cache로 둡니다.
나중에 Postgres나 graph DB로 옮겨도 artifact 구조가 유지되므로 lock-in이 작습니다.
```

핵심 문장:

> DB는 serving layer이고, artifact가 source of truth입니다.

---

## 질문 3. “Pydantic과 YAML 둘 다 있으면 drift 안 나나?”

이건 선임이 반드시 물어볼 수 있습니다.

답변 방향:

```text
현재 runtime validation은 Pydantic 기준입니다.
YAML은 schema documentation, taxonomy, relation config, display/index contract 역할입니다.
향후 JSON Schema generation 또는 ontology registry를 통해 drift를 CI에서 잡을 계획입니다.
```

여기서 숨기면 안 됩니다.
이건 실제 리스크이므로 솔직히 말하는 게 좋습니다.

---

## 질문 4. “AI가 claim을 과장하면 어떻게 막나?”

답변 방향:

```text
AI claim은 반드시 supported_by_quotes를 가져야 합니다.
validator는 quote exact match, reference validity, numeric consistency를 검사합니다.
또한 claim에는 direct/inferred, support_level, requires_inference 같은 필드를 둬서 원문 직접 진술과 시스템 추론을 분리합니다.
```

추가로 말하면 좋습니다.

> quote exactness는 해결했지만 claim overreach는 별도 평가 문제가 있으므로 gold-set evaluation이 필요합니다.

이 답변이 좋습니다.
무조건 “막을 수 있다”고 하면 신뢰도가 떨어집니다.

---

## 질문 5. “Graph DB 써야 하는 거 아닌가?”

답변 방향:

```text
현재 단계에서는 SQLite + edges table + FTS로 충분합니다.
Graph DB는 multi-hop query가 복잡해지고 object volume이 커진 뒤 검토하는 게 맞습니다.
지금은 graph DB보다 schema registry, validator, evaluation이 더 급합니다.
```

제 의견도 같습니다.

> 지금 graph DB로 가는 건 이릅니다. 먼저 artifact 품질과 query contract를 잡아야 합니다.

---

# 8. 제가 바꾸고 싶은 핵심 설계 10가지

## 1. `ExtractionRun` / `RunManifest` 추가

모든 artifact에 다음을 연결해야 합니다.

```text
run_id
schema_version
pipeline_version
taxonomy_version
sector_pack_version
model_version
source_document_hash
created_at
```

이건 재현성과 디버깅의 핵심입니다.

---

## 2. `OntologyRegistry` 도입

object type별로 다음을 중앙화해야 합니다.

```text
artifact file
text fields
reference fields
relation projection
validation rules
indexing rules
display rules
trace rules
```

이게 없으면 object type이 늘어날수록 유지보수가 어려워집니다.

---

## 3. `ExternalFactorExposure` 방향성 재설계

현재 `effect_direction`은 모호합니다.

반드시 다음처럼 바꾸는 게 좋습니다.

```text
metric_effect_when_factor_increases
company_effect_when_factor_increases
metric_effect_when_factor_decreases
company_effect_when_factor_decreases
pass_through
offsets
```

이건 시나리오 질문의 품질을 좌우합니다.

---

## 4. `confidence` 분리

단일 confidence는 위험합니다.

```text
extraction_confidence
classification_confidence
evidence_strength
materiality_hint
ranking_score
```

로 분리하는 게 좋습니다.

---

## 5. `boilerplate_score`와 `specificity_score` 추가

SEC risk factor를 제대로 다루려면 필수입니다.

```text
generic legal risk
company-specific operating risk
project-specific risk
metric-linked risk
newly changed risk
```

이 차이를 잡아야 합니다.

---

## 6. `ResearchClaim.requires_inference` 추가

원문 직접 진술과 시스템 추론을 분리해야 합니다.

```text
requires_inference = false
→ filing이 직접 말함

requires_inference = true
→ quote를 근거로 시스템이 한 단계 해석함
```

이건 답변 신뢰성에 중요합니다.

---

## 7. `CanonicalEntity` 또는 alias registry 추가

프로젝트, 세그먼트, 계약, 제품, 규제기관을 안정적으로 연결하려면 필요합니다.

처음부터 거대한 NER 시스템을 만들 필요는 없습니다.
하지만 alias registry는 빨리 넣는 게 좋습니다.

---

## 8. Numeric dimension 강화

`FinancialMetricValue`에는 segment/geography/product dimension을 넣는 것이 좋습니다.

그래야 `SegmentPerformance`를 급하게 추가하지 않아도 됩니다.

---

## 9. Evaluation gold set 구축

최소 10~20개 filing에 대해 사람이 gold quote, gold claim, gold exposure를 만들어야 합니다.

이 없으면 품질 개선이 감으로만 진행됩니다.

---

## 10. MCP query contract 명확화

MCP는 단순 검색 도구가 아니라 evidence graph query tool이어야 합니다.

추천 tool shape:

```text
query_objects
trace_object
trace_claim
get_evidence_chain
compare_periods
get_company_profile
get_factor_exposures
check_numeric_support
```

특히 `trace_object`와 `get_evidence_chain`은 중요합니다.

---

# 9. 발전 로드맵

## Phase 1. 안정화

목표는 “깨지지 않는 evidence graph”입니다.

해야 할 일:

```text
Pydantic/YAML source of truth 정리
OntologyRegistry 도입
RunManifest 추가
quote exact match validator 강화
reference validator 강화
index builder registry화
MCP compact response registry화
```

성공 기준:

```text
schema validation 100%
invalid reference 0%
quote exact match 100%
index rebuild deterministic
```

---

## Phase 2. 품질 평가

목표는 “좋아 보이는 시스템”이 아니라 “측정 가능한 시스템”입니다.

해야 할 일:

```text
gold filing set 구축
candidate recall 측정
claim support precision 측정
factor mapping accuracy 측정
object duplicate rate 측정
answer trace success 측정
```

성공 기준:

```text
gold quote candidate recall이 일정 기준 이상
unsupported claim rate 감소
duplicate object rate 감소
answer마다 quote trace 가능
```

---

## Phase 3. materiality/ranking

목표는 “많이 찾기”가 아니라 “중요한 것을 위에 올리기”입니다.

해야 할 일:

```text
boilerplate_score
specificity_score
section_weight
metric_link_score
business_activity_link_score
temporal_change_score
ranking formula
```

성공 기준:

```text
broad question에서 상위 결과가 사람이 보기에도 중요한 이슈
generic risk factor가 상단을 도배하지 않음
새로 바뀐 리스크가 잘 올라옴
```

---

## Phase 4. sector pack 확장

목표는 sector별 리서치 품질을 올리는 것입니다.

우선순위는 개인적으로 이렇게 봅니다.

```text
energy_lng
big_tech
semiconductor
bank
consumer
healthcare
industrial
SaaS
```

각 sector pack에는 다음을 넣어야 합니다.

```text
business activity taxonomy
external factor taxonomy
metric dictionary
aliases
Korean synonyms
object grouping hints
section search hints
```

---

## Phase 5. 선택적 object 추가

이 단계에서 새 object type을 추가하는 게 맞습니다.

우선순위:

```text
1. ProjectMilestone
2. ContractTerm
3. RegulatoryProceeding
4. CapitalStructureItem
5. GuidanceItem
6. SegmentPerformance
```

단, `SegmentPerformance`는 먼저 metric dimension으로 버티는 게 좋습니다.

---

# 10. 최종 판단

현재 KRW Ontology의 방향은 좋습니다.
특히 다음 5개 판단은 강하게 긍정적입니다.

```text
EvidenceQuote를 1급 객체로 둔 것
ResearchClaim을 중간 계층으로 둔 것
AI를 selector/extractor로 제한하고 code validation을 둔 것
File-canonical, DB-cache 구조를 택한 것
ExternalFactorExposure를 별도 객체로 둔 것
```

하지만 다음 5개는 반드시 보강해야 합니다.

```text
Pydantic/YAML/registry의 source of truth 정리
quote recall과 candidate generation 평가
confidence/materiality/evidence_strength 분리
ExternalFactorExposure의 방향성 재정의
temporal identity와 change detection 강화
```

제가 선임개발자에게 보고한다면 핵심 메시지는 이렇게 잡겠습니다.

> 이 시스템은 RAG 검색기가 아니라 SEC filing을 근거 추적 가능한 research graph로 변환하는 evidence ontology다. 현재 핵심 레이어링은 맞고, schema를 무작정 늘리기보다 registry, validator, evaluation, materiality ranking을 먼저 강화해야 한다. 새 object type은 ProjectMilestone, ContractTerm, RegulatoryProceeding처럼 반복 조회·UI 표현·cross-period 비교 가치가 검증된 경우에만 추가한다.

그리고 가장 중요한 방향은 이겁니다.

```text
스키마 확장
< 검증 강화
< 평가 체계
< materiality ranking
< sector taxonomy
< query/display contract 자동화
```

즉, 지금은 “더 많은 객체”가 아니라 **더 믿을 수 있는 객체**를 만드는 단계입니다.


두번째 답변>>>>

아니요. **이대로 “앞으로 안 바꿀 최선의 스키마”로 보이지는 않습니다.**

방향은 맞습니다. 하지만 지금 상태로 freeze하면 나중에 반드시 크게 손댈 가능성이 높습니다.
특히 다음 5개 때문에 그렇습니다.

```text
1. Entity identity가 약함
2. ExternalFactorExposure 방향성이 모호함
3. Risk / Headwind / GrowthDriver 중복 가능성이 큼
4. 숫자 객체의 dimension/context가 아직 부족함
5. 새 객체 추가 시 pipeline 전체가 깨지는 구조임
```

더 직설적으로 말하면:

> 현재 스키마는 좋은 v1 후보입니다.
> 하지만 “수정 비용이 너무 크니 한 번에 최고의 스키마를 박아야 한다”는 상황이라면, **지금 그대로 박으면 안 됩니다.**

한 번 크게 바꿀 기회가 있다면, 지금 해야 합니다.

---

# 1. 핵심 판단

제가 보기엔 현재 설계는 **80점짜리 좋은 설계**입니다.
하지만 고정해도 되는 **95점짜리 장기 스키마**는 아닙니다.

현재 구조의 핵심 흐름은 좋습니다.

```text
SourceDocument
→ SourceSpan
→ EvidenceQuote
→ ResearchClaim
→ RiskFactor / GrowthDriver / Headwind / BusinessActivity / ExternalFactorExposure
→ Edge
→ CompanyBusinessProfile / TemporalLink / TrendObservation / ChangeEvent
```

이 흐름 자체는 유지해야 합니다.
특히 `EvidenceQuote` 중심 구조는 매우 좋습니다.

하지만 장기적으로 진짜 안 바뀌려면, 스키마는 다음 3가지를 반드시 가져야 합니다.

```text
1. 모든 객체가 EvidenceQuote까지 trace 가능해야 함
2. 모든 객체가 canonical entity와 연결 가능해야 함
3. 새 object type이 생겨도 index, validator, MCP, display가 덜 깨져야 함
```

현재는 1번은 잘 잡혀 있습니다.
하지만 2번과 3번이 약합니다.

그래서 제 결론은 이겁니다.

> 지금 설계의 철학은 맞다.
> 하지만 current object list를 그대로 최종 schema로 freeze하는 것은 위험하다.
> 한 번 더 큰 정리를 하고 freeze해야 한다.

---

# 2. “최고의 스키마”는 객체를 많이 만드는 게 아닙니다

여기서 중요한 착각을 피해야 합니다.

“앞으로 안 바뀌는 최고의 스키마”는 모든 객체를 미리 다 만들어놓는 스키마가 아닙니다.

나쁜 방향은 이겁니다.

```text
RiskFactor
GrowthDriver
Headwind
ContractTerm
ProjectMilestone
SegmentPerformance
CapitalStructureItem
RegulatoryProceeding
GuidanceItem
NewsEvent
MarketDataPoint
...
```

객체 타입을 계속 늘리는 방식은 나중에 더 복잡해집니다.

좋은 방향은 이겁니다.

```text
변하지 않을 core schema를 강하게 고정
+
sector/taxonomy/display/query는 registry로 확장
+
새 객체는 기존 generic structure 안에서 subtype으로 흡수
```

즉, 앞으로 안 바뀌게 하려면 **객체 타입을 많이 만드는 게 아니라, 변하지 않을 상위 구조를 잘 잡아야 합니다.**

제가 생각하는 불변 코어는 이겁니다.

```text
Evidence
Entity
Claim
Metric
Business Activity
Business Factor
External Exposure
Event / Obligation
Temporal Change
Support Link
Edge
```

이 정도가 장기적으로 안정적인 뼈대입니다.

---

# 3. 지금 스키마에서 반드시 바꿔야 할 부분

## 3.1 `CanonicalEntity`가 필요합니다

현재 스키마에서 가장 큰 빈틈입니다.

지금 구조는 quote, claim, risk, activity는 있는데, “그 claim이 정확히 어떤 실체를 가리키는가”가 약합니다.

예를 들어 이런 것들입니다.

```text
CP2
CP2 LNG
CP2 Project
the CP2 export facility

AWS
Amazon Web Services

Services
Apple Services

Henry Hub
natural gas price
feed gas price
```

이 표현들이 같은 대상인지 아닌지 안정적으로 잡아야 합니다.

그래서 `CanonicalEntity`가 필요합니다.

예시:

```json
{
  "id": "entity:project:VG:CP2",
  "entity_type": "project",
  "canonical_name": "CP2 LNG",
  "aliases": [
    "CP2",
    "CP2 Project",
    "CP2 LNG project",
    "the CP2 export facility"
  ],
  "ticker": "VG"
}
```

그리고 quote나 claim은 이 entity를 참조해야 합니다.

```json
{
  "claim_text": "The company expects CP2 to begin operations in late 2029.",
  "entities": ["entity:project:VG:CP2"]
}
```

이게 없으면 나중에 반드시 문제가 생깁니다.

특히 이런 질문에서 무너집니다.

```text
CP2 관련 리스크가 작년 대비 바뀌었나?
AWS 마진 압박 요인은 뭐야?
Services segment 성장률은 어떻게 변했어?
Henry Hub 하락이 이 회사에 좋은 거야?
```

제 판단으로는 `CanonicalEntity`는 선택이 아니라 필수입니다.

---

## 3.2 `ExternalFactorExposure`는 지금 형태로 freeze하면 안 됩니다

현재 `ExternalFactorExposure`는 방향은 좋지만, 필드 구조가 위험합니다.

특히 `effect_direction`이 모호합니다.

예를 들어:

```json
{
  "factor": "natural_gas_price",
  "impact_channel": "cost_of_revenue",
  "effect_direction": "negative"
}
```

여기서 negative가 무슨 뜻인지 애매합니다.

```text
천연가스 가격 상승이 회사에 부정적이라는 뜻인가?
천연가스 가격 하락이 cost_of_revenue에 부정적이라는 뜻인가?
cost_of_revenue가 올라간다는 뜻인가?
margin에 부정적이라는 뜻인가?
```

이건 시나리오 질문에서 치명적입니다.

이 객체는 반드시 이렇게 바꿔야 합니다.

```json
{
  "factor": "natural_gas_price",
  "benchmark": "Henry Hub",
  "impact_channel": "cost_of_revenue",

  "metric_effect_when_factor_increases": "increases",
  "company_effect_when_factor_increases": "negative",

  "metric_effect_when_factor_decreases": "decreases",
  "company_effect_when_factor_decreases": "positive",

  "pass_through_mechanism": "partial_or_unknown",
  "offsetting_factors": [
    "fixed-fee contracts",
    "hedging",
    "contractual pass-through"
  ],

  "evidence_grade": "direct",
  "time_horizon": "ongoing"
}
```

이렇게 해야 이런 질문에 답할 수 있습니다.

```text
Henry Hub가 떨어지면 VG에 좋은가?
금리가 올라가면 이 회사에 나쁜가?
LNG spot price 상승은 revenue에 좋은가, margin에 나쁜가?
```

제가 강하게 말하면:

> `ExternalFactorExposure`는 지금 스키마의 핵심 자산입니다.
> 그래서 더더욱 지금 형태로 고정하면 안 됩니다.
> 방향성 필드를 지금 고쳐야 합니다.

---

## 3.3 `RiskFactor`, `GrowthDriver`, `Headwind`는 내부적으로 통합하는 게 낫습니다

현재는 세 객체가 따로 있습니다.

```text
RiskFactor
GrowthDriver
Headwind
```

제품 관점에서는 좋습니다.
사용자에게 보여줄 때는 이 구분이 직관적입니다.

하지만 내부 schema 관점에서는 중복이 생길 수 있습니다.

예를 들어:

```text
higher feed gas costs
```

이건 상황에 따라 셋 중 하나가 됩니다.

| 상황             | 객체                       |
| -------------- | ------------------------ |
| 미래 가능성         | RiskFactor               |
| 현재 실적 부담       | Headwind                 |
| 가격 하락 시 반대로 유리 | GrowthDriver 또는 Exposure |
| 외부요인 경로        | ExternalFactorExposure   |

즉, 같은 의미가 여러 객체로 갈라질 수 있습니다.

장기적으로는 내부적으로 `BusinessFactor`라는 base object를 두고, role을 나누는 게 좋습니다.

예:

```json
{
  "id": "business_factor:VG:natural_gas_cost_pressure",
  "object_type": "BusinessFactor",
  "factor_role": "headwind",
  "polarity": "negative",
  "status": "actual_or_recent",
  "category": "commodity_cost",
  "name": "Feed gas cost pressure",
  "description": "Natural gas prices may affect feed gas procurement costs.",
  "affected_channels": ["cost_of_revenue", "gross_margin", "cash_flow"]
}
```

그리고 product view에서는 이렇게 보여주면 됩니다.

```text
factor_role = risk       → RiskFactor 카드
factor_role = headwind   → Headwind 카드
factor_role = driver     → GrowthDriver 카드
```

즉:

```text
내부 schema: BusinessFactor
외부 display: RiskFactor / GrowthDriver / Headwind
```

이게 더 오래 갑니다.

현재처럼 완전히 별도 object type으로 고정하면 나중에 중복 제거가 어렵습니다.

---

## 3.4 숫자 객체는 `MetricObservation` 중심으로 정리하는 게 좋습니다

현재 숫자 레이어는 다음처럼 나뉘어 있습니다.

```text
XBRLFact
FinancialMetricValue
DerivedMetricValue
NumericEvidence
CalculatedNumericSupport
```

방향은 맞습니다.
하지만 장기적으로는 `MetricObservation`이라는 더 일반적인 중심 객체가 필요합니다.

왜냐하면 숫자에서 중요한 건 value 자체보다 context입니다.

예를 들어 revenue 하나만 봐도 이런 차이가 있습니다.

```text
FY revenue
Q1 revenue
segment revenue
geography revenue
reported revenue
adjusted revenue
constant currency revenue
USD millions
USD thousands
```

이걸 제대로 잡으려면 숫자 객체에 dimension이 있어야 합니다.

추천 구조:

```json
{
  "id": "metric:AAPL:FY2025:services:revenue",
  "object_type": "MetricObservation",
  "metric_name": "revenue",
  "value": 96169000000,
  "unit": "USD",
  "scale": "ones",
  "period": "FY2025",
  "period_type": "duration",
  "source_type": "xbrl",
  "source_fact_id": "xbrl:...",
  "dimensions": {
    "segment": "Services",
    "geography": null,
    "product": null
  },
  "normalization": "reported"
}
```

이렇게 하면 `SegmentPerformance`를 별도 객체로 급하게 추가하지 않아도 됩니다.

즉:

```text
SegmentPerformance
= segment dimension이 붙은 MetricObservation들의 view
```

이게 더 안정적입니다.

---

## 3.5 `SupportLink` 또는 `EvidenceSupport`가 필요합니다

현재 객체들은 대체로 이런 필드를 갖습니다.

```text
supported_by_claims
supported_by_quotes
related_metrics
```

이 방식은 직관적입니다.
하지만 객체가 늘어날수록 필드가 계속 늘어납니다.

더 안정적인 방식은 지원 관계를 별도 객체로 빼는 것입니다.

예:

```json
{
  "id": "support:001",
  "target_object_id": "risk:VG:feed_gas_cost_risk",
  "target_object_type": "BusinessFactor",

  "support_object_id": "quote:VG:FY2025:10K:item7:123",
  "support_object_type": "EvidenceQuote",

  "support_role": "primary_evidence",
  "support_strength": "direct",
  "inference_level": "direct_quote",
  "evidence_grade": "direct"
}
```

또 다른 예:

```json
{
  "id": "support:002",
  "target_object_id": "assumption:VG:feed_gas_cost_margin",
  "target_object_type": "AssumptionCandidate",

  "support_object_id": "metric:VG:FY2025:cost_of_revenue",
  "support_object_type": "MetricObservation",

  "support_role": "numeric_support",
  "support_strength": "derived",
  "inference_level": "calculated"
}
```

이 구조의 장점은 큽니다.

| 장점                      | 설명                                                |
| ----------------------- | ------------------------------------------------- |
| 새 객체 추가가 쉬움             | 모든 객체에 `supported_by_*` 필드를 새로 만들 필요가 적음          |
| evidence strength 표현 가능 | direct, indirect, derived 구분 가능                   |
| trace가 쉬움               | 객체 → support link → quote/claim/metric            |
| contradiction도 표현 가능    | supporting evidence뿐 아니라 conflicting evidence도 가능 |
| MCP 응답이 안정적             | 모든 객체를 같은 방식으로 trace 가능                           |

제가 보기엔 `SupportLink`는 장기 schema에서 거의 필수입니다.

현재의 `Edge`가 관계를 표현한다면, `SupportLink`는 “근거의 성격”을 표현합니다.

```text
Edge = 관계
SupportLink = 근거/추론/검증 관계
```

둘은 다릅니다.

---

## 3.6 `RunManifest`는 반드시 있어야 합니다

이건 객체 스키마라기보다 provenance 스키마입니다.

모든 추출 결과는 어떤 실행에서 나왔는지 알아야 합니다.

예:

```json
{
  "run_id": "run:VG:FY2025:10K:2026-05-16:001",
  "source_document_id": "source:VG:FY2025:10K",
  "schema_version": "1.0.0",
  "pipeline_version": "0.9.0",
  "taxonomy_version": "energy_lng:0.4.0",
  "sector_pack_version": "energy_lng:0.4.0",
  "model_name": "gpt-...",
  "created_at": "2026-05-16T00:00:00Z",
  "source_text_hash": "..."
}
```

이게 없으면 나중에 이런 질문에 답할 수 없습니다.

```text
왜 지난번 결과와 이번 결과가 다르지?
이 객체는 어떤 모델 버전으로 생성됐지?
taxonomy 바꾼 뒤 어떤 factor mapping이 바뀌었지?
schema v1.1로 migration된 객체와 v1.0 객체를 어떻게 구분하지?
```

수정 비용이 큰 시스템일수록 `RunManifest`는 필수입니다.

---

## 3.7 `OntologyRegistry`가 없으면 결국 또 바꿔야 합니다

현재 설명을 보면 새 object type을 추가할 때 다음을 다 바꿔야 합니다.

```text
Pydantic model
YAML schema
relations.yaml
validators
artifact index
agent index
MCP response
skill docs
display contract
```

이건 구조적으로 위험합니다.

이 문제를 해결하려면 object type별 설정을 registry로 중앙화해야 합니다.

예:

```yaml
BusinessFactor:
  artifact_file: business_factors.jsonl

  text_fields:
    - name
    - description
    - category

  filter_fields:
    - ticker
    - period
    - factor_role
    - category
    - materiality

  reference_fields:
    entities: CanonicalEntity
    related_metrics: MetricObservation
    related_activities: BusinessActivity

  support:
    allowed_support_types:
      - EvidenceQuote
      - ResearchClaim
      - MetricObservation

  compact_display:
    title: name
    subtitle: factor_role
    body: description

  trace:
    default_depth: 3
    include:
      - SupportLink
      - EvidenceQuote
      - SourceSpan
      - SourceDocument
```

이 registry가 있으면 object type을 추가해도 index, validation, MCP, display가 같은 계약을 공유할 수 있습니다.

제 생각에는 “한 번에 최고의 스키마”를 원한다면, `OntologyRegistry`가 핵심입니다.

> 최고의 스키마는 모든 미래 객체를 미리 아는 스키마가 아니라, 미래 객체가 생겨도 core pipeline을 덜 바꾸는 스키마입니다.

---

# 4. 제가 추천하는 최종형 스키마 구조

제가 지금 한 번만 크게 바꿀 수 있다면, 최종 구조를 이렇게 잡겠습니다.

```text
1. Provenance Layer
   - RunManifest
   - SchemaVersion
   - TaxonomyVersion

2. Source Layer
   - SourceDocument
   - SourceSpan

3. Evidence Layer
   - EvidenceQuote
   - LanguageSignal
   - SupportLink

4. Entity Layer
   - CanonicalEntity
   - EntityMention

5. Claim Layer
   - ResearchClaim

6. Numeric Layer
   - XBRLFact
   - MetricObservation
   - DerivedMetricObservation
   - NumericEvidence
   - CalculationSupport

7. Business Semantic Layer
   - BusinessActivity
   - BusinessFactor
   - ExternalFactorExposure
   - AssumptionCandidate

8. Event / Obligation Layer
   - BusinessEvent
   - AgreementTerm / Obligation

9. Company Context Layer
   - CompanyBusinessProfile
   - TemporalLink
   - TrendObservation
   - ChangeEvent

10. Graph / Serving Layer
   - Edge
   - OntologyRegistry
   - AgentIndex
```

여기서 핵심은 `BusinessFactor`, `BusinessEvent`, `AgreementTerm`, `CanonicalEntity`, `SupportLink`, `MetricObservation`입니다.

이것들이 있으면 많은 미래 객체를 흡수할 수 있습니다.

---

# 5. 현재 객체를 어떻게 바꾸면 좋은가

## 5.1 유지할 것

아래는 그대로 유지해도 됩니다. 단, 필드 보강은 필요합니다.

```text
SourceDocument
SourceSpan
EvidenceQuote
LanguageSignal
ResearchClaim
BusinessActivity
CompanyBusinessProfile
TemporalLink
TrendObservation
ChangeEvent
Edge
```

특히 `EvidenceQuote` 중심 구조는 유지해야 합니다.
이건 현재 설계의 가장 강한 부분입니다.

---

## 5.2 바꿔야 할 것

### `RiskFactor`, `GrowthDriver`, `Headwind`

외부 product view로는 유지해도 됩니다.
하지만 내부적으로는 `BusinessFactor`로 통합하는 게 좋습니다.

```text
RiskFactor     → BusinessFactor(factor_role = risk)
GrowthDriver   → BusinessFactor(factor_role = driver)
Headwind       → BusinessFactor(factor_role = headwind)
```

이렇게 하면 중복과 migration 비용이 줄어듭니다.

---

### `ExternalFactorExposure`

반드시 방향성 필드를 재설계해야 합니다.

기존:

```text
effect_direction = positive / negative / mixed / uncertain
```

추천:

```text
metric_effect_when_factor_increases
company_effect_when_factor_increases
metric_effect_when_factor_decreases
company_effect_when_factor_decreases
pass_through_mechanism
offsetting_factors
```

이건 정말 중요합니다.

---

### `FinancialMetricValue`, `DerivedMetricValue`

`MetricObservation` 중심으로 통합하는 게 좋습니다.

```text
FinancialMetricValue      → MetricObservation(source_type = reported/xbrl)
DerivedMetricValue        → MetricObservation(source_type = derived)
CalculatedNumericSupport  → CalculationSupport
```

이렇게 하면 segment, geography, product, adjusted/reported context를 자연스럽게 처리할 수 있습니다.

---

## 5.3 추가해야 할 것

제가 보기엔 최소 추가해야 할 것은 이 6개입니다.

```text
RunManifest
OntologyRegistry
CanonicalEntity
EntityMention
SupportLink
BusinessEvent
AgreementTerm / Obligation
```

정확히는 7개지만, `EntityMention`은 `CanonicalEntity`의 보조 객체입니다.

---

# 6. `BusinessEvent`를 넣어야 하는 이유

장기적으로 `ProjectMilestone`, `RegulatoryProceeding`, `GuidanceItem` 같은 객체를 계속 따로 만들면 schema가 무거워집니다.

이걸 한 번에 흡수하려면 `BusinessEvent`가 좋습니다.

예:

```json
{
  "id": "event:VG:CP2:COD:FY2029",
  "object_type": "BusinessEvent",
  "event_type": "project_milestone",
  "event_subtype": "commercial_operation_date",
  "status": "targeted",
  "target_date_or_period": "late 2029",
  "actual_date": null,
  "related_entities": ["entity:project:VG:CP2"],
  "affected_channels": ["revenue", "capex", "cash_flow"],
  "materiality": "high"
}
```

또는 regulatory proceeding:

```json
{
  "id": "event:VG:CP2:FERC_APPROVAL",
  "object_type": "BusinessEvent",
  "event_type": "regulatory_proceeding",
  "authority": "FERC",
  "status": "pending",
  "related_entities": ["entity:project:VG:CP2"],
  "affected_channels": ["project_timing", "capex", "revenue"]
}
```

또는 guidance:

```json
{
  "id": "event:AAPL:FY2026:guidance_update:revenue",
  "object_type": "BusinessEvent",
  "event_type": "guidance_update",
  "metric_name": "revenue",
  "period_target": "FY2026",
  "status": "updated"
}
```

이렇게 하면 event성 객체를 하나의 구조로 흡수할 수 있습니다.

즉:

```text
ProjectMilestone      = BusinessEvent(event_type = project_milestone)
RegulatoryProceeding  = BusinessEvent(event_type = regulatory_proceeding)
GuidanceItem          = BusinessEvent(event_type = guidance_update)
ChangeEvent           = BusinessEvent(event_type = disclosure_change)로 일부 흡수 가능
```

다만 `ChangeEvent`는 ontology 내부 변화 감지 객체로 남겨도 됩니다.

---

# 7. `AgreementTerm / Obligation`을 넣어야 하는 이유

계약, debt, lease, purchase commitment, take-or-pay, covenant는 event가 아닙니다.
이건 조건과 의무입니다.

그래서 `BusinessEvent`로 다 흡수하면 안 됩니다.

별도로 `AgreementTerm` 또는 `Obligation`이 필요합니다.

예:

```json
{
  "id": "obligation:VG:SPA:customer_x:001",
  "object_type": "AgreementTerm",
  "agreement_type": "offtake_contract",
  "counterparty": "Customer X",
  "term_start": "2026",
  "term_end": "2046",
  "pricing_mechanism": "fixed_fee_or_formula_based",
  "volume_commitment": "unknown",
  "minimum_commitment": "take_or_pay",
  "revenue_or_cost_relevance": "revenue",
  "related_entities": ["entity:project:VG:CP2"]
}
```

Debt도 여기에 넣을 수 있습니다.

```json
{
  "id": "obligation:AAPL:notes:2030",
  "object_type": "AgreementTerm",
  "agreement_type": "debt_instrument",
  "principal_amount": 1000000000,
  "interest_rate": "fixed",
  "maturity_date": "2030-05-01",
  "liquidity_relevance": "medium"
}
```

이렇게 하면 `ContractTerm`과 `CapitalStructureItem`을 어느 정도 통합할 수 있습니다.

---

# 8. 최종적으로 freeze해야 할 핵심 contract

앞으로 진짜 안 바꾸고 싶은 부분은 객체명이 아니라 이 계약입니다.

모든 object는 공통적으로 이런 필드를 가져야 합니다.

```json
{
  "id": "...",
  "object_type": "...",
  "schema_version": "...",
  "ticker": "...",
  "company_entity_id": "...",
  "period": "...",
  "source_document_ids": ["..."],
  "extraction_run_id": "...",
  "created_at": "...",

  "entities": ["..."],
  "metrics": ["..."],

  "evidence_strength": "...",
  "extraction_confidence": "...",
  "classification_confidence": "...",
  "materiality_hint": "...",

  "extensions": {}
}
```

`extensions`는 중요합니다.
하지만 남용하면 안 됩니다.

원칙은 이겁니다.

```text
core query/display/validation에 필요한 필드는 정식 field
실험적이거나 sector-specific한 필드는 extensions
반복 사용되면 정식 field로 승격
```

이 구조가 있으면 미래 변경 비용이 줄어듭니다.

---

# 9. 무엇을 지금 freeze하면 안 되는가

아래는 지금 상태로 고정하면 위험합니다.

```text
1. RiskFactor / GrowthDriver / Headwind를 완전히 별도 객체로 고정
2. ExternalFactorExposure.effect_direction을 단일 방향 필드로 고정
3. FinancialMetricValue에 dimension 없는 구조로 고정
4. supported_by_claims / supported_by_quotes만으로 evidence 관계를 고정
5. CanonicalEntity 없이 TemporalLink만으로 기간 비교를 처리
6. Pydantic과 YAML을 이중 source of truth로 유지
7. object type 추가 시 index/MCP/display를 수동 수정하는 구조
```

이건 나중에 비싸게 돌아옵니다.

---

# 10. 제가 보는 최종 답

질문이 이거죠.

> “이게 앞으로도 안 바꿀 최선으로 보여?”

제 답은 명확합니다.

```text
아니요.
지금 스키마는 철학과 방향은 맞지만, 최종 freeze하기에는 아직 부족합니다.
```

하지만 동시에 이렇게도 말할 수 있습니다.

```text
완전히 갈아엎을 필요는 없습니다.
EvidenceQuote 중심 구조는 유지해야 합니다.
SourceSpan → EvidenceQuote → ResearchClaim 흐름은 맞습니다.
```

진짜 필요한 건 갈아엎기가 아니라 **상위 추상화 보강**입니다.

최종 freeze 전에 반드시 넣어야 할 것은 이것들입니다.

```text
1. CanonicalEntity
2. EntityMention
3. SupportLink / EvidenceSupport
4. RunManifest
5. OntologyRegistry
6. ExternalFactorExposure 방향성 재설계
7. MetricObservation + dimensions
8. BusinessFactor base model
9. BusinessEvent
10. AgreementTerm / Obligation
```

이걸 넣고 나면 훨씬 오래 갑니다.

---

# 11. 제일 중요한 한 문장

제가 이 프로젝트 의사결정권자라면 이렇게 결정합니다.

> 현재 스키마를 그대로 freeze하지 않는다.
> EvidenceQuote 중심 철학은 유지하되, `Entity`, `SupportLink`, `MetricObservation`, `BusinessFactor`, `BusinessEvent`, `AgreementTerm`, `OntologyRegistry`를 추가한 뒤 v1으로 고정한다.

이렇게 해야 나중에 새 섹터, 새 질문, 새 UI, 새 MCP tool이 생겨도 schema migration이 아니라 taxonomy/registry/display 확장으로 버틸 수 있습니다.

현재 설계는 좋은 출발점입니다.
하지만 **수정 비용이 엄청 크다면 지금 멈추면 안 됩니다. 지금 한 번 더 정리하고 freeze해야 합니다.**



3번째 답변>>>

제 기준으로 점수를 매기면 이렇습니다.

| 상태                  |          점수 |
| ------------------- | ----------: |
| 지금 네가 처음 설명한 원래 스키마 |     **82점** |
| 내가 직전에 제안한 보강안      |     **92점** |
| 실제로 freeze해도 되는 최종형 | **98~100점** |

직전에 말한 보강안은 꽤 좋지만, 아직 100점은 아닙니다.
왜냐하면 아직 다음 4개가 완전히 정리되지 않았기 때문입니다.

```text
1. object identity와 object observation이 분리되지 않음
2. SupportLink / EvidenceSupport가 core contract로 충분히 강제되지 않음
3. taxonomy / registry / display / validation 계약이 하나로 묶이지 않음
4. table, numeric, event, agreement 같은 비문장형 SEC 정보까지 완전히 흡수하지 못함
```

그래서 내가 진짜로 “수정 비용이 엄청 크고, 한 번에 최고로 가야 한다”고 판단한다면, 최종 설계는 이렇게 갑니다.

---

# 결론부터

지금 freeze하면 안 됩니다.

최종 스키마는 다음 철학으로 가야 합니다.

```text
Evidence-first
+ Entity-centered
+ Observation-based
+ SupportLink-governed
+ Registry-driven
+ Taxonomy-versioned
+ Display/query-ready
```

가장 중요한 한 문장은 이겁니다.

> 모든 의미 객체는 “영구적 실체”가 아니라 “특정 source에서 관측된 observation”이어야 하고, 영구적 identity는 `CanonicalEntity`와 `TemporalLink`가 담당해야 한다.

이걸 안 하면 나중에 기간 비교, 프로젝트 추적, segment 추적, 계약 추적에서 반드시 깨집니다.

---

# 1. 100점짜리 스키마의 핵심 구조

최종 레이어는 이렇게 잡는 게 맞습니다.

```text
0. Governance / Registry Layer
   - RunManifest
   - OntologyRegistry
   - TaxonomyTerm
   - ValidationReport

1. Source Layer
   - SourceDocument
   - SourceLocation
   - SourceSpan
   - SourceTable
   - SourceTableCell

2. Evidence Layer
   - EvidenceQuote
   - LanguageSignal
   - SupportLink

3. Entity Layer
   - CanonicalEntity
   - EntityMention

4. Claim Layer
   - ResearchClaim

5. Numeric Layer
   - XBRLFact
   - MetricObservation
   - Calculation

6. Business Semantic Layer
   - BusinessActivity
   - BusinessFactor
   - ExternalFactorExposure
   - AssumptionCandidate
   - AgreementTerm
   - BusinessEvent

7. Company Context Layer
   - CompanyBusinessProfile
   - TemporalLink
   - TrendObservation
   - ChangeEvent

8. Graph / Serving Layer
   - Edge
   - AgentIndex / MCP Query Contract
```

이 구조가 좋은 이유는 단순합니다.

```text
RiskFactor, GrowthDriver, Headwind → BusinessFactor role로 흡수
ProjectMilestone, RegulatoryProceeding, GuidanceItem → BusinessEvent subtype으로 흡수
ContractTerm, CapitalStructureItem, Lease, Covenant → AgreementTerm subtype으로 흡수
SegmentPerformance → MetricObservation dimension view로 흡수
```

즉, 새 객체 타입을 무한히 늘리지 않고도 미래 use case를 흡수합니다.

---

# 2. 최종 스키마의 가장 중요한 원칙

## 2.1 객체는 두 종류로 나눠야 합니다

이게 정말 중요합니다.

```text
Canonical object
= 기간을 넘어 유지되는 실체

Observation object
= 특정 filing / 특정 period / 특정 source에서 관측된 정보
```

예를 들면:

```text
CanonicalEntity:
- CP2 LNG project
- AWS
- Apple Services
- Henry Hub
- FERC
- Customer X

Observation:
- FY2025 10-K에서 CP2 COD가 late 2029로 언급됨
- FY2026 Q1 10-Q에서 CP2 regulatory approval risk가 언급됨
- FY2025 10-K에서 natural gas price exposure가 언급됨
```

이걸 분리해야 합니다.

나쁜 구조:

```text
ProjectMilestone 자체가 영구 객체처럼 존재
```

좋은 구조:

```text
CanonicalEntity: CP2 LNG
BusinessEvent observation: FY2025 filing에서 관측된 CP2 milestone
TemporalLink: FY2024 event observation과 FY2025 event observation 연결
```

이 구조가 있어야 “작년 대비 뭐가 바뀌었나?”를 안정적으로 답할 수 있습니다.

---

# 3. 공통 object envelope

모든 객체는 최소한 이 공통 필드를 가져야 합니다.

```json
{
  "id": "string",
  "object_type": "string",
  "schema_version": "1.0.0",
  "object_version": "string",
  "extraction_run_id": "run:...",
  "source_boundary": "sec_filing",
  "ticker": "AAPL",
  "company_entity_id": "entity:company:AAPL",
  "period": "FY2025",
  "fiscal_year": 2025,
  "fiscal_quarter": null,
  "source_document_ids": ["doc:AAPL:2025:10K"],
  "created_at": "2026-05-16T00:00:00Z",
  "updated_at": null,
  "status": "active",
  "validation_status": "passed",
  "extraction_method": "hybrid_code_llm",
  "extraction_confidence": 0.91,
  "classification_confidence": 0.88,
  "evidence_strength": "direct",
  "materiality_hint": "medium",
  "materiality_basis": [],
  "specificity_score": 0.72,
  "boilerplate_score": 0.18,
  "support_link_ids": [],
  "related_entity_ids": [],
  "related_metric_ids": [],
  "extensions": {}
}
```

중요한 점은 `confidence` 하나로 뭉치면 안 됩니다.

반드시 분리해야 합니다.

```text
extraction_confidence
classification_confidence
evidence_strength
materiality_hint
specificity_score
boilerplate_score
```

`confidence = high` 같은 단일 필드는 나중에 해석이 무조건 꼬입니다.

---

# 4. Governance / Registry Layer

## 4.1 `RunManifest`

이건 필수입니다.

모든 extraction artifact는 어떤 실행에서 나온 건지 알아야 합니다.

```json
{
  "id": "run:VG:FY2025:10K:2026-05-16:001",
  "object_type": "RunManifest",
  "schema_version": "1.0.0",
  "pipeline_version": "0.9.0",
  "taxonomy_version": "energy_lng:0.4.0",
  "sector_pack_version": "energy_lng:0.4.0",
  "model_name": "gpt-5.5-pro",
  "source_document_ids": ["doc:VG:FY2025:10K"],
  "source_text_hash": "sha256:...",
  "created_at": "2026-05-16T00:00:00Z",
  "status": "completed"
}
```

이게 없으면 나중에 이런 질문에 답 못 합니다.

```text
왜 지난번과 추출 결과가 달라졌나?
어떤 taxonomy version으로 추출했나?
schema migration 전후 결과 차이는 뭔가?
```

---

## 4.2 `OntologyRegistry`

이건 진짜 핵심입니다.

스키마 수정 비용이 큰 이유는 object type 하나 추가할 때 index, validator, MCP, display, docs를 다 바꿔야 하기 때문입니다.

그러면 object type별 계약을 registry에 박아야 합니다.

예:

```yaml
BusinessFactor:
  artifact_file: business_factors.jsonl

  text_fields:
    - name
    - description
    - mechanism
    - category

  filter_fields:
    - ticker
    - period
    - factor_role
    - materiality_hint
    - category
    - affected_channels

  reference_fields:
    related_entity_ids: CanonicalEntity
    related_metric_ids: MetricObservation
    related_activity_ids: BusinessActivity
    support_link_ids: SupportLink

  required_validators:
    - object_id_unique
    - controlled_terms_valid
    - support_links_resolve
    - materiality_basis_required_if_high
    - no_high_materiality_with_high_boilerplate

  compact_display:
    title: name
    subtitle: factor_role
    body: mechanism
    evidence_field: support_link_ids

  mcp:
    searchable: true
    traceable: true
    comparable_across_periods: true
```

이게 있으면 새 객체가 생겨도 pipeline 전체가 덜 깨집니다.

`OntologyRegistry` 없이 freeze하면, 그건 100점이 아닙니다.

---

## 4.3 `TaxonomyTerm`

factor, metric, activity, category, channel 같은 값은 raw string으로 두면 안 됩니다.

반드시 taxonomy term으로 관리해야 합니다.

```json
{
  "id": "term:factor:natural_gas_price",
  "object_type": "TaxonomyTerm",
  "taxonomy_name": "external_factor",
  "term_key": "natural_gas_price",
  "display_name": "Natural gas price",
  "aliases": {
    "en": ["natural gas price", "Henry Hub", "feed gas price"],
    "ko": ["천연가스 가격", "헨리허브", "원료가스 가격"]
  },
  "sector_relevance": ["energy_lng", "utilities"],
  "version": "1.0.0"
}
```

이게 있어야 한국어 질문도 canonical term으로 매핑됩니다.

```text
천연가스 가격 떨어지면?
→ natural_gas_price
→ Henry Hub
→ feed_gas_cost
→ cost_of_revenue
→ margin
```

---

# 5. Source Layer

## 5.1 `SourceDocument`

```json
{
  "id": "doc:AAPL:2025:10K:0000320193-25-000079",
  "object_type": "SourceDocument",
  "ticker": "AAPL",
  "company_entity_id": "entity:company:AAPL",
  "cik": "0000320193",
  "accession_number": "0000320193-25-000079",
  "document_type": "10-K",
  "filing_date": "2025-10-31",
  "period_end_date": "2025-09-27",
  "fiscal_year": 2025,
  "fiscal_quarter": null,
  "source_url": "...",
  "raw_text_hash": "sha256:...",
  "clean_text_hash": "sha256:...",
  "metadata_ref": "metadata.json"
}
```

`filing_date`와 `period_end_date`는 반드시 분리해야 합니다.
이거 섞으면 시계열 분석이 망가집니다.

---

## 5.2 `SourceLocation`

이 객체가 100점 설계에서 중요합니다.

왜냐하면 SEC filing 근거는 문장만 있는 게 아닙니다.

```text
text span
table row
table cell
XBRL fact
footnote
section heading
```

을 모두 가리킬 수 있어야 합니다.

```json
{
  "id": "loc:AAPL:2025:10K:item7:span:042",
  "object_type": "SourceLocation",
  "source_document_id": "doc:AAPL:2025:10K:...",
  "location_type": "text_span",
  "section_name": "item7",
  "start_char": 120034,
  "end_char": 120912,
  "table_id": null,
  "cell_id": null,
  "xbrl_fact_id": null
}
```

`EvidenceQuote`는 이 `SourceLocation`을 참조합니다.

이렇게 해야 table, XBRL, text quote를 같은 trace 시스템에서 다룰 수 있습니다.

---

## 5.3 `SourceSpan`

```json
{
  "id": "span:AAPL:2025:10K:item7:042",
  "object_type": "SourceSpan",
  "source_document_id": "doc:AAPL:2025:10K:...",
  "source_location_id": "loc:AAPL:2025:10K:item7:span:042",
  "section_name": "item7",
  "section_path": ["part2", "item7"],
  "span_index": 42,
  "text": "...",
  "text_hash": "sha256:...",
  "section_detection_confidence": 0.97
}
```

---

## 5.4 `SourceTable` / `SourceTableCell`

이것도 넣는 게 좋습니다.

10-K/10-Q에서 중요한 숫자와 계약 조건은 table에 많이 있습니다.
table을 단순 text로 flatten하면 나중에 추적이 약해집니다.

```json
{
  "id": "table:AAPL:2025:10K:liquidity:001",
  "object_type": "SourceTable",
  "source_document_id": "doc:AAPL:2025:10K:...",
  "section_name": "liquidity",
  "caption": "Contractual Obligations",
  "start_char": 300120,
  "end_char": 302800,
  "table_hash": "sha256:..."
}
```

```json
{
  "id": "cell:AAPL:2025:10K:liquidity:001:r3:c4",
  "object_type": "SourceTableCell",
  "source_table_id": "table:AAPL:2025:10K:liquidity:001",
  "row_index": 3,
  "column_index": 4,
  "row_label": "Long-term debt",
  "column_label": "2028",
  "cell_text": "$1,000",
  "normalized_value": 1000000000,
  "unit": "USD",
  "scale": "millions"
}
```

이게 있어야 debt maturity, lease obligation, purchase commitment 같은 분석이 강해집니다.

---

# 6. Evidence Layer

## 6.1 `EvidenceQuote`

기존 핵심 구조는 유지합니다.
단, 더 강하게 만듭니다.

```json
{
  "id": "quote:AAPL:2025:10K:item7:042:cand07",
  "object_type": "EvidenceQuote",
  "source_document_id": "doc:AAPL:2025:10K:...",
  "source_span_id": "span:AAPL:2025:10K:item7:042",
  "source_location_id": "loc:AAPL:2025:10K:item7:span:042",
  "candidate_id": "cand07",
  "quote_text": "Our gross margin may be affected by changes in component costs.",
  "quote_hash": "sha256:...",
  "quote_type": "margin_risk",
  "section_name": "item7",
  "start_char": 112,
  "end_char": 178,
  "absolute_start_char": 120146,
  "absolute_end_char": 120212,
  "quote_exact_match_verified": true,
  "candidate_generation_method": "sentence_split_v3",
  "language_signal_ids": ["signal:..."],
  "specificity_score": 0.74,
  "boilerplate_score": 0.21
}
```

`quote_exact_match_verified`는 production에서 무조건 true여야 합니다.
여기서 타협하면 안 됩니다.

---

## 6.2 `LanguageSignal`

```json
{
  "id": "signal:quote:001:may_adversely_affect",
  "object_type": "LanguageSignal",
  "evidence_quote_id": "quote:...",
  "signal_text": "may adversely affect",
  "polarity": "negative",
  "modality": "potential",
  "temporality": "future_or_potential",
  "certainty": "possible",
  "legal_tone": "cautionary",
  "mitigation": "not_specified"
}
```

단순히 `potential_negative` 같은 태그만 두지 말고, 축을 나누는 게 좋습니다.

```text
polarity
modality
temporality
certainty
legal_tone
mitigation
```

---

## 6.3 `SupportLink`

이게 100점 설계의 핵심입니다.

현재처럼 모든 객체에 `supported_by_quotes`, `supported_by_claims`를 직접 박는 구조는 오래 못 갑니다.

최종 스키마에서는 근거 관계를 별도 객체로 빼야 합니다.

```json
{
  "id": "support:business_factor:001:quote:003",
  "object_type": "SupportLink",

  "target_object_id": "factor:AAPL:FY2025:component_cost_pressure",
  "target_object_type": "BusinessFactor",

  "support_object_id": "quote:AAPL:2025:10K:item7:042:cand07",
  "support_object_type": "EvidenceQuote",

  "support_role": "primary_evidence",
  "stance": "supports",
  "support_strength": "direct",
  "inference_level": "direct_quote",
  "evidence_grade": "direct",

  "explanation": "The quote explicitly states that component costs may affect gross margin.",
  "confidence": 0.94
}
```

`SupportLink`는 단순 edge보다 중요합니다.

```text
Edge = 객체 간 의미 관계
SupportLink = 근거, 추론, 검증 관계
```

이렇게 구분해야 합니다.

그리고 `SupportLink.stance`는 다음을 허용해야 합니다.

```text
supports
refutes
qualifies
contextualizes
calculates
derived_from
```

이게 있으면 나중에 conflicting evidence도 처리할 수 있습니다.

예:

```text
한 quote는 원가 상승 리스크를 말함
다른 quote는 hedging/contract pass-through로 완화된다고 말함
```

이런 경우 둘 다 저장하고 stance를 다르게 둬야 합니다.

---

# 7. Entity Layer

## 7.1 `CanonicalEntity`

이건 반드시 추가해야 합니다.

```json
{
  "id": "entity:project:VG:CP2_LNG",
  "object_type": "CanonicalEntity",
  "entity_type": "project",
  "canonical_name": "CP2 LNG",
  "ticker": "VG",
  "parent_entity_id": "entity:company:VG",
  "aliases": [
    "CP2",
    "CP2 Project",
    "CP2 LNG project",
    "the CP2 export facility"
  ],
  "external_ids": {},
  "sector_tags": ["energy_lng"],
  "status": "active"
}
```

Entity type은 최소 이 정도가 필요합니다.

```text
company
segment
product
service
project
facility
contract
counterparty
regulatory_authority
geography
external_factor
benchmark
metric
business_activity
```

이 객체 없이는 multi-period 비교가 부정확해집니다.

---

## 7.2 `EntityMention`

```json
{
  "id": "mention:VG:2025:10K:item7:CP2:001",
  "object_type": "EntityMention",
  "source_document_id": "doc:VG:2025:10K:...",
  "source_location_id": "loc:...",
  "mention_text": "CP2 Project",
  "canonical_entity_id": "entity:project:VG:CP2_LNG",
  "mention_type": "project",
  "normalization_confidence": 0.96
}
```

`CanonicalEntity`는 영구 identity이고, `EntityMention`은 문서 안에서 실제 등장한 표현입니다.

---

# 8. Claim Layer

## 8.1 `ResearchClaim`

`ResearchClaim`은 atomic assertion이어야 합니다.

```json
{
  "id": "claim:VG:FY2025:feed_gas_cost_exposure:001",
  "object_type": "ResearchClaim",

  "claim_text": "The company is exposed to natural gas price changes through feed gas procurement costs.",
  "normalized_claim_text": "Natural gas price changes affect feed gas procurement costs.",

  "claim_type": "business_exposure",
  "subject_entity_id": "entity:company:VG",
  "predicate": "is_exposed_to",
  "object_entity_id": "term:factor:natural_gas_price",

  "related_entity_ids": [
    "entity:factor:natural_gas_price",
    "entity:activity:VG:feed_gas_procurement"
  ],

  "related_metric_ids": [
    "term:metric:cost_of_revenue",
    "term:metric:gross_margin"
  ],

  "polarity": "negative_or_positive_depending_on_direction",
  "modality": "actual_exposure",
  "temporality": "ongoing",
  "time_horizon": "ongoing",

  "requires_inference": true,
  "inference_level": "single_step",
  "evidence_directness": "indirect",

  "materiality_hint": "medium",
  "materiality_basis": [
    "linked_to_primary_cost_source",
    "linked_to_margin_channel"
  ]
}
```

`requires_inference`는 필수입니다.

원문이 직접 말한 것과 시스템이 한 단계 해석한 것을 구분해야 합니다.

```text
requires_inference = false
→ filing이 직접 말함

requires_inference = true
→ quote를 근거로 시스템이 해석함
```

이거 없으면 결국 RAG식 해석 혼합 문제가 다시 생깁니다.

---

# 9. Numeric Layer

## 9.1 `XBRLFact`

raw fact는 그대로 둡니다.

```json
{
  "id": "xbrl:AAPL:2025:Revenue:context123",
  "object_type": "XBRLFact",
  "source_document_id": "doc:AAPL:2025:10K:...",
  "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
  "value": 391035000000,
  "unit": "USD",
  "decimals": "-6",
  "context_ref": "context123",
  "period_start": "2024-09-29",
  "period_end": "2025-09-27",
  "period_type": "duration",
  "dimensions": {}
}
```

---

## 9.2 `MetricObservation`

`FinancialMetricValue`와 `DerivedMetricValue`는 최종적으로 `MetricObservation`으로 통합하는 게 좋습니다.

```json
{
  "id": "metric:AAPL:FY2025:company:revenue:reported",
  "object_type": "MetricObservation",

  "metric_term_id": "term:metric:revenue",
  "metric_name": "revenue",

  "value": 391035000000,
  "unit": "USD",
  "scale": "ones",

  "period": "FY2025",
  "period_start": "2024-09-29",
  "period_end": "2025-09-27",
  "period_type": "duration",

  "source_type": "xbrl",
  "source_fact_ids": [
    "xbrl:AAPL:2025:Revenue:context123"
  ],

  "normalization": "reported",

  "dimensions": {
    "segment": null,
    "geography": null,
    "product": null,
    "customer_type": null
  },

  "confidence": 1.0
}
```

중요한 건 `dimensions`입니다.

이걸 넣으면 `SegmentPerformance`를 별도 객체로 만들 필요가 줄어듭니다.

```text
AWS revenue
Services revenue
US revenue
International revenue
product revenue
```

는 모두 `MetricObservation`의 dimension으로 처리할 수 있습니다.

---

## 9.3 `Calculation`

```json
{
  "id": "calc:AAPL:FY2025:gross_margin_rate",
  "object_type": "Calculation",

  "calculation_type": "derived_metric",
  "formula": "gross_profit / revenue",
  "input_metric_ids": [
    "metric:AAPL:FY2025:company:gross_profit:reported",
    "metric:AAPL:FY2025:company:revenue:reported"
  ],
  "output_metric_id": "metric:AAPL:FY2025:company:gross_margin_rate:derived",

  "calculation_method": "deterministic_code",
  "rounding_policy": "full_precision_then_display",
  "validation_status": "passed"
}
```

숫자 계산은 AI에게 맡기면 안 됩니다.
`Calculation` 객체가 있어야 합니다.

---

# 10. Business Semantic Layer

## 10.1 `BusinessActivity`

```json
{
  "id": "activity:VG:feed_gas_procurement",
  "object_type": "BusinessActivity",

  "name": "Feed gas procurement",
  "activity_type": "cost_source",
  "description": "The company procures feed gas as an input for LNG production.",

  "revenue_relevance": "none",
  "cost_relevance": "primary",
  "capex_relevance": "none",
  "working_capital_relevance": "medium",

  "related_entity_ids": [
    "entity:company:VG"
  ],
  "related_factor_term_ids": [
    "term:factor:natural_gas_price"
  ],
  "related_metric_term_ids": [
    "term:metric:cost_of_revenue",
    "term:metric:gross_margin"
  ],

  "sector_tags": ["energy_lng"]
}
```

`capex_relevance`는 넣는 게 좋습니다.
프로젝트 중심 회사에서는 중요합니다.

---

## 10.2 `BusinessFactor`

이게 최종 설계에서 `RiskFactor`, `GrowthDriver`, `Headwind`를 흡수합니다.

```json
{
  "id": "factor:VG:FY2025:feed_gas_cost_pressure",
  "object_type": "BusinessFactor",

  "name": "Feed gas cost pressure",
  "description": "Changes in natural gas prices may affect feed gas procurement costs and margins.",

  "factor_roles": [
    "risk",
    "headwind"
  ],

  "category": "commodity_cost",
  "occurrence_status": "potential",
  "company_polarity": "negative",
  "modality": "conditional",
  "time_horizon": "ongoing",

  "affected_channels": [
    "cost_of_revenue",
    "gross_margin",
    "cash_flow"
  ],

  "related_activity_ids": [
    "activity:VG:feed_gas_procurement"
  ],

  "related_factor_term_ids": [
    "term:factor:natural_gas_price"
  ],

  "related_metric_term_ids": [
    "term:metric:cost_of_revenue",
    "term:metric:gross_margin"
  ],

  "materiality_hint": "medium",
  "materiality_basis": [
    "linked_to_primary_cost_source",
    "linked_to_margin_channel"
  ],

  "specificity_score": 0.79,
  "boilerplate_score": 0.12
}
```

이렇게 하면 product에서는 여전히 다음처럼 보여줄 수 있습니다.

```text
factor_roles contains risk       → RiskFactor view
factor_roles contains driver     → GrowthDriver view
factor_roles contains headwind   → Headwind view
```

즉, 내부 schema는 통합하고, 외부 UI는 분리합니다.

이게 장기적으로 훨씬 좋습니다.

---

## 10.3 `ExternalFactorExposure`

이 객체는 최종형에서 반드시 재설계해야 합니다.

단순 `effect_direction`은 버립니다.

대신 `scenario_effects`를 둡니다.

```json
{
  "id": "exposure:VG:FY2025:natural_gas_price:feed_gas_cost",
  "object_type": "ExternalFactorExposure",

  "external_factor_term_id": "term:factor:natural_gas_price",
  "benchmark_entity_id": "entity:benchmark:Henry_Hub",

  "exposure_type": "input_cost",
  "mechanism": "Natural gas prices affect feed gas procurement costs, which can flow through cost of revenue and margin.",

  "related_activity_ids": [
    "activity:VG:feed_gas_procurement"
  ],

  "affected_channels": [
    "cost_of_revenue",
    "gross_margin",
    "cash_flow"
  ],

  "scenario_effects": [
    {
      "factor_change": "increase",
      "affected_channel": "cost_of_revenue",
      "metric_direction": "increase",
      "company_effect": "negative",
      "effect_certainty": "likely",
      "lag": "short_to_medium",
      "conditions": ["assuming no full contractual pass-through"]
    },
    {
      "factor_change": "decrease",
      "affected_channel": "cost_of_revenue",
      "metric_direction": "decrease",
      "company_effect": "positive",
      "effect_certainty": "likely",
      "lag": "short_to_medium",
      "conditions": ["assuming no full contractual pass-through"]
    }
  ],

  "pass_through_mechanism": "partial_or_unknown",
  "offsetting_factors": [
    "fixed_fee_contracts",
    "hedging",
    "contractual_pass_through"
  ],

  "evidence_grade": "direct_or_inferred",
  "materiality_hint": "medium"
}
```

이 구조가 좋은 이유는 non-price factor도 처리할 수 있기 때문입니다.

예를 들어 regulatory approval은 “increase/decrease”가 아니라 “approved/delayed/denied”입니다.

```json
{
  "factor_change": "approval_delayed",
  "affected_channel": "project_timing",
  "metric_direction": "delayed",
  "company_effect": "negative"
}
```

이렇게 쓸 수 있습니다.

따라서 `scenario_effects` 방식이 가장 오래 갑니다.

---

## 10.4 `AssumptionCandidate`

```json
{
  "id": "assumption:VG:FY2025:feed_gas_cost_margin_sensitivity",
  "object_type": "AssumptionCandidate",

  "name": "Feed gas cost margin sensitivity",
  "assumption_type": "margin_sensitivity",
  "assumption_text": "Natural gas price changes may affect cost of revenue and gross margin.",

  "value_hint": null,
  "value_range_low": null,
  "value_range_high": null,
  "unit": null,

  "basis_type": "filing_disclosed_exposure",
  "related_metric_term_ids": [
    "term:metric:cost_of_revenue",
    "term:metric:gross_margin"
  ],

  "applicability_conditions": [
    "depends on contract pass-through",
    "depends on hedge coverage"
  ],

  "caveats": [
    "The filing does not quantify the sensitivity."
  ]
}
```

이 객체는 valuation conclusion이 아닙니다.
그냥 valuation input 후보입니다.

---

## 10.5 `AgreementTerm`

이 객체는 `ContractTerm`, `CapitalStructureItem`, `Lease`, `Covenant`, `PurchaseCommitment`를 흡수합니다.

```json
{
  "id": "agreement:VG:FY2025:offtake_contract:customer_x",
  "object_type": "AgreementTerm",

  "agreement_type": "offtake_contract",
  "agreement_subtype": "lng_spa",

  "name": "LNG offtake agreement with Customer X",
  "party_entity_ids": [
    "entity:company:VG",
    "entity:counterparty:Customer_X"
  ],

  "term_start": "2026",
  "term_end": "2046",
  "date_certainty": "approximate",

  "pricing_mechanism": "fixed_fee_or_formula_based",
  "volume_commitment": "unknown",
  "minimum_commitment": "take_or_pay",
  "termination_condition": "not_disclosed",

  "economic_role": "revenue",
  "affected_channels": [
    "revenue",
    "cash_flow"
  ],

  "related_entity_ids": [
    "entity:project:VG:CP2_LNG"
  ]
}
```

Debt도 같은 객체로 처리할 수 있습니다.

```json
{
  "id": "agreement:AAPL:FY2025:debt:notes_2030",
  "object_type": "AgreementTerm",

  "agreement_type": "debt_instrument",
  "instrument_name": "2030 Notes",
  "principal_amount": 1000000000,
  "currency": "USD",
  "interest_rate_type": "fixed",
  "maturity_date": "2030-05-01",

  "economic_role": "financing",
  "affected_channels": [
    "interest_expense",
    "liquidity",
    "cash_flow"
  ]
}
```

이렇게 하면 `CapitalStructureItem`을 별도 top-level object로 만들 필요가 줄어듭니다.

---

## 10.6 `BusinessEvent`

이 객체는 `ProjectMilestone`, `RegulatoryProceeding`, `GuidanceItem`, `LitigationEvent`, `FinancingEvent`를 흡수합니다.

```json
{
  "id": "event:VG:FY2025:CP2:target_COD_late_2029",
  "object_type": "BusinessEvent",

  "event_type": "project_milestone",
  "event_subtype": "commercial_operation_date",

  "name": "Target commercial operation date for CP2",
  "event_status": "targeted",

  "date_expression": "late 2029",
  "date_type": "period_expression",
  "date_start": "2029-10-01",
  "date_end": "2029-12-31",
  "date_certainty": "approximate",

  "related_entity_ids": [
    "entity:project:VG:CP2_LNG"
  ],

  "affected_channels": [
    "revenue_timing",
    "capex",
    "cash_flow"
  ],

  "materiality_hint": "high",
  "materiality_basis": [
    "linked_to_major_project",
    "linked_to_future_revenue_timing"
  ]
}
```

Regulatory proceeding도 같은 객체로 처리합니다.

```json
{
  "id": "event:VG:FY2025:CP2:FERC_approval",
  "object_type": "BusinessEvent",

  "event_type": "regulatory_proceeding",
  "authority_entity_id": "entity:regulator:FERC",
  "event_status": "pending",

  "related_entity_ids": [
    "entity:project:VG:CP2_LNG"
  ],

  "affected_channels": [
    "project_timing",
    "capex",
    "revenue_timing"
  ]
}
```

Guidance도 같은 객체로 처리할 수 있습니다.

```json
{
  "id": "event:MSFT:FY2026Q1:guidance:capex",
  "object_type": "BusinessEvent",

  "event_type": "guidance_update",
  "metric_term_id": "term:metric:capex",
  "period_target": "FY2026",
  "event_status": "guided",

  "guided_value_low": null,
  "guided_value_high": null,
  "guidance_text": "The company expects capital expenditures to increase."
}
```

이렇게 하면 schema가 훨씬 덜 변합니다.

---

# 11. Company Context Layer

## 11.1 `CompanyBusinessProfile`

이건 최종 근거라기보다 query planning map입니다.

```json
{
  "id": "profile:VG:FY2025",
  "object_type": "CompanyBusinessProfile",

  "company_entity_id": "entity:company:VG",
  "period": "FY2025",

  "primary_business_activity_ids": [
    "activity:VG:lng_sales",
    "activity:VG:feed_gas_procurement",
    "activity:VG:project_development"
  ],

  "primary_revenue_sources": [
    "lng_sales",
    "fixed_fee_contracts"
  ],

  "primary_cost_sources": [
    "feed_gas",
    "liquefaction_operations",
    "interest_expense"
  ],

  "major_project_entity_ids": [
    "entity:project:VG:CP2_LNG"
  ],

  "key_external_factor_term_ids": [
    "term:factor:natural_gas_price",
    "term:factor:lng_market_price",
    "term:factor:regulatory_approval",
    "term:factor:interest_rate"
  ],

  "key_metric_term_ids": [
    "term:metric:revenue",
    "term:metric:gross_margin",
    "term:metric:capex",
    "term:metric:cash_flow"
  ],

  "query_vocabulary": {
    "ko": ["천연가스", "LNG 가격", "규제 승인", "CP2", "마진"],
    "en": ["natural gas", "LNG price", "regulatory approval", "CP2", "margin"]
  }
}
```

Profile은 final evidence가 아닙니다.
답변할 때는 profile에서 바로 말하지 말고, profile을 통해 관련 quote/claim/factor를 찾아야 합니다.

---

## 11.2 `TemporalLink`

```json
{
  "id": "temporal:factor:VG:feed_gas_cost_pressure:FY2024:FY2025",
  "object_type": "TemporalLink",

  "from_object_id": "factor:VG:FY2024:feed_gas_cost_pressure",
  "to_object_id": "factor:VG:FY2025:feed_gas_cost_pressure",

  "link_type": "same_underlying_issue",
  "identity_confidence": 0.92,

  "match_features": [
    "same_external_factor",
    "same_cost_channel",
    "same_business_activity",
    "similar_quote_language"
  ],

  "change_summary": "The issue remained present with similar language."
}
```

TemporalLink는 문자열 매칭만으로 만들면 안 됩니다.
entity, factor, metric, quote similarity, section, support chain을 같이 봐야 합니다.

---

## 11.3 `TrendObservation`

```json
{
  "id": "trend:AAPL:FY2023-FY2025:services_revenue",
  "object_type": "TrendObservation",

  "metric_term_id": "term:metric:revenue",
  "dimension_filter": {
    "segment": "Services"
  },

  "periods": ["FY2023", "FY2024", "FY2025"],
  "metric_observation_ids": [
    "metric:AAPL:FY2023:services:revenue",
    "metric:AAPL:FY2024:services:revenue",
    "metric:AAPL:FY2025:services:revenue"
  ],

  "trend_direction": "increasing",
  "calculation_method": "deterministic_code",
  "summary": "Services revenue increased across the observed periods."
}
```

---

## 11.4 `ChangeEvent`

```json
{
  "id": "change:VG:FY2024-FY2025:CP2_timeline",
  "object_type": "ChangeEvent",

  "change_type": "target_date_changed",
  "from_object_id": "event:VG:FY2024:CP2:target_COD",
  "to_object_id": "event:VG:FY2025:CP2:target_COD",

  "related_entity_ids": [
    "entity:project:VG:CP2_LNG"
  ],

  "change_direction": "delayed",
  "change_summary": "The target timing for the CP2 milestone appears later than in the prior filing.",
  "confidence": 0.86
}
```

---

# 12. Graph / Edge Layer

## 12.1 `Edge`

`Edge`는 일반 관계입니다.

```json
{
  "id": "edge:claim001:describes:factor001",
  "object_type": "Edge",

  "from_object_id": "claim:VG:FY2025:feed_gas_cost_exposure:001",
  "from_object_type": "ResearchClaim",

  "to_object_id": "factor:VG:FY2025:feed_gas_cost_pressure",
  "to_object_type": "BusinessFactor",

  "relation_type": "describes_factor",
  "edge_class": "semantic",

  "created_by": "deterministic_projection",
  "confidence": 0.93
}
```

중요한 점:

```text
SupportLink는 evidence relationship
Edge는 semantic relationship
```

둘을 섞지 않는 게 좋습니다.

---

# 13. 최종 파일 구조

파일 기반 canonical artifact라면 이렇게 갑니다.

```text
artifacts/
  manifest.json
  ontology_registry.yaml
  validation_report.json

  taxonomy/
    terms.yaml
    sector_packs/
      generic.yaml
      energy_lng.yaml
      semiconductors.yaml
      big_tech.yaml
      banking.yaml

  source_documents.jsonl
  source_locations.jsonl
  source_spans.jsonl
  source_tables.jsonl
  source_table_cells.jsonl

  evidence_quotes.jsonl
  language_signals.jsonl
  support_links.jsonl

  canonical_entities.jsonl
  entity_mentions.jsonl

  research_claims.jsonl

  xbrl_facts.jsonl
  metric_observations.jsonl
  calculations.jsonl

  business_activities.jsonl
  business_factors.jsonl
  external_factor_exposures.jsonl
  assumption_candidates.jsonl
  agreement_terms.jsonl
  business_events.jsonl

  company_business_profiles.jsonl
  temporal_links.jsonl
  trend_observations.jsonl
  change_events.jsonl

  edges.jsonl

  agent_index.sqlite
```

이 구조면 나중에 schema object를 추가하는 일이 크게 줄어듭니다.

---

# 14. 기존 객체에서 바꿔야 하는 것

## 14.1 `RiskFactor`, `GrowthDriver`, `Headwind`

최종 스키마에서는 top-level canonical object로 두지 않는 게 낫습니다.

대신:

```text
RiskFactor     = BusinessFactor where factor_roles contains "risk"
GrowthDriver   = BusinessFactor where factor_roles contains "growth_driver"
Headwind       = BusinessFactor where factor_roles contains "headwind"
```

즉, 이 셋은 **generated view**로 두세요.

파일도 꼭 필요하면 이렇게 둡니다.

```text
risk_factors.jsonl      = generated view
growth_drivers.jsonl    = generated view
headwinds.jsonl         = generated view
```

canonical source는 `business_factors.jsonl`입니다.

---

## 14.2 `FinancialMetricValue`, `DerivedMetricValue`

최종 스키마에서는 다음으로 통합합니다.

```text
FinancialMetricValue → MetricObservation(source_type = reported/xbrl)
DerivedMetricValue   → MetricObservation(source_type = derived)
```

`Calculation`이 derived metric의 근거를 담당합니다.

---

## 14.3 `NumericEvidence`, `CalculatedNumericSupport`

이 둘은 다음으로 정리합니다.

```text
NumericEvidence → SupportLink + MetricObservation
CalculatedNumericSupport → Calculation + SupportLink
```

필요하면 `NumericEvidence`를 generated view로 유지하면 됩니다.

---

## 14.4 `ProjectMilestone`, `RegulatoryProceeding`, `GuidanceItem`

top-level object로 추가하지 마세요.

대신:

```text
ProjectMilestone      → BusinessEvent(event_type = project_milestone)
RegulatoryProceeding  → BusinessEvent(event_type = regulatory_proceeding)
GuidanceItem          → BusinessEvent(event_type = guidance_update)
```

이게 훨씬 오래 갑니다.

---

## 14.5 `ContractTerm`, `CapitalStructureItem`

별도 top-level object로 나눌 필요 없습니다.

```text
ContractTerm          → AgreementTerm(agreement_type = contract / offtake / supply)
CapitalStructureItem  → AgreementTerm(agreement_type = debt_instrument / credit_facility)
Lease                 → AgreementTerm(agreement_type = lease)
Covenant              → AgreementTerm(agreement_type = covenant)
```

이 구조가 더 강합니다.

---

# 15. 최종 validator 규칙

100점 스키마는 객체 정의만으로 끝나면 안 됩니다.
validator가 같이 있어야 합니다.

필수 validation gate:

```text
1. 모든 object id는 unique
2. 모든 source_document_id는 실제 존재
3. EvidenceQuote.quote_text는 SourceSpan.text에 exact match
4. EvidenceQuote absolute char range는 clean_text_hash 기준으로 유효
5. 모든 SupportLink target/source object는 존재
6. 모든 Edge from/to object는 존재
7. 모든 taxonomy term id는 현재 taxonomy version에 존재
8. MetricObservation unit/scale/period_type은 유효
9. Derived MetricObservation은 Calculation을 반드시 가짐
10. high materiality object는 materiality_basis를 반드시 가짐
11. ExternalFactorExposure는 최소 하나의 scenario_effect를 가져야 함
12. BusinessFactor는 factor_roles가 비어 있으면 안 됨
13. BusinessEvent는 event_type과 event_status가 controlled term이어야 함
14. AgreementTerm은 agreement_type이 controlled term이어야 함
15. requires_inference=false claim은 direct EvidenceQuote support가 있어야 함
16. source_boundary가 다른 객체는 명시적 cross_source policy 없이 섞지 않음
17. boilerplate_score가 높은 객체는 high materiality로 자동 승격 불가
18. TemporalLink는 identity_confidence와 match_features를 가져야 함
```

이 규칙이 없으면 스키마가 아무리 좋아도 답변 품질이 흔들립니다.

---

# 16. MCP Query Contract

최종적으로 MCP는 단순 검색기가 아니어야 합니다.

필수 tool contract는 이 정도입니다.

```text
query_objects
trace_object
trace_claim
get_evidence_chain
get_company_profile
get_factor_exposures
compare_periods
get_metric_series
get_business_events
get_agreement_terms
check_numeric_support
```

각 응답은 항상 이 구조를 가져야 합니다.

```json
{
  "object": {},
  "compact_summary": "...",
  "support_chain": [
    {
      "support_link": {},
      "evidence_quote": {},
      "source_location": {},
      "source_document": {}
    }
  ],
  "confidence_breakdown": {
    "extraction_confidence": 0.91,
    "classification_confidence": 0.88,
    "evidence_strength": "direct",
    "materiality_hint": "medium"
  }
}
```

답변 생성 AI가 이걸 받으면 hallucination 위험이 훨씬 줄어듭니다.

---

# 17. 100점 설계의 최종 객체 목록

최종적으로 freeze할 객체는 이겁니다.

```text
Governance
- RunManifest
- OntologyRegistry
- TaxonomyTerm
- ValidationReport

Source
- SourceDocument
- SourceLocation
- SourceSpan
- SourceTable
- SourceTableCell

Evidence
- EvidenceQuote
- LanguageSignal
- SupportLink

Entity
- CanonicalEntity
- EntityMention

Claim
- ResearchClaim

Numeric
- XBRLFact
- MetricObservation
- Calculation

Business Semantic
- BusinessActivity
- BusinessFactor
- ExternalFactorExposure
- AssumptionCandidate
- AgreementTerm
- BusinessEvent

Company Context
- CompanyBusinessProfile
- TemporalLink
- TrendObservation
- ChangeEvent

Graph
- Edge
```

이 정도가 내가 보기에 freeze 가능한 최종 구조입니다.

너무 많아 보일 수 있지만, 실제로는 객체를 늘린 게 아니라 미래 객체를 흡수할 상위 추상화를 만든 겁니다.

---

# 18. 이 설계가 왜 100점에 가까운가

이 설계는 미래 변경을 이렇게 흡수합니다.

| 미래 요구                       | 새 top-level schema 필요 여부 | 처리 방식                                             |
| --------------------------- | -----------------------: | ------------------------------------------------- |
| Project milestone           |                      불필요 | `BusinessEvent(event_type=project_milestone)`     |
| Regulatory approval         |                      불필요 | `BusinessEvent(event_type=regulatory_proceeding)` |
| Debt maturity               |                      불필요 | `AgreementTerm(agreement_type=debt_instrument)`   |
| Lease obligation            |                      불필요 | `AgreementTerm(agreement_type=lease)`             |
| Segment performance         |                      불필요 | `MetricObservation.dimensions.segment`            |
| Guidance                    |                      불필요 | `BusinessEvent(event_type=guidance_update)`       |
| Risk / headwind / driver 중복 |                       해결 | `BusinessFactor.factor_roles`                     |
| Market scenario             |                       해결 | `ExternalFactorExposure.scenario_effects`         |
| Multi-period identity       |                       해결 | `CanonicalEntity` + `TemporalLink`                |
| Evidence trace              |                       해결 | `SupportLink`                                     |
| 새 sector                    |            schema 변경 불필요 | `TaxonomyTerm` + sector pack                      |
| 새 UI block                  |            schema 변경 최소화 | `OntologyRegistry.compact_display`                |
| 새 MCP query                 |            schema 변경 최소화 | registry 기반 searchable/filterable/traceable       |

이게 핵심입니다.

---

# 19. 최종 점수

내가 직전에 말한 보강안은 **92점**입니다.

이번 최종 설계까지 반영하면 **98~100점**입니다.

다만 현실적으로 소프트웨어에서 “절대 안 바뀌는 100점”은 object field가 하나도 안 바뀐다는 뜻이 아닙니다.
진짜 100점의 의미는 이겁니다.

```text
core ontology contract는 안 바뀐다.
새로운 도메인, 섹터, UI, 질문 유형은 taxonomy, registry, subtype, extensions로 흡수된다.
breaking migration이 거의 필요 없어지는 구조다.
```

그 기준에서는 위 설계가 100점에 가장 가깝습니다.

---

# 20. 최종 결정

내가 의사결정자라면 이렇게 합니다.

```text
1. 기존 EvidenceQuote 중심 철학은 유지한다.
2. RiskFactor / GrowthDriver / Headwind는 BusinessFactor view로 통합한다.
3. FinancialMetricValue / DerivedMetricValue는 MetricObservation으로 통합한다.
4. ProjectMilestone / RegulatoryProceeding / GuidanceItem은 BusinessEvent로 흡수한다.
5. ContractTerm / CapitalStructureItem은 AgreementTerm으로 흡수한다.
6. CanonicalEntity, EntityMention, SupportLink, RunManifest, OntologyRegistry는 반드시 추가한다.
7. ExternalFactorExposure는 scenario_effects 구조로 재설계한다.
8. SourceLocation / SourceTable / SourceTableCell을 넣어 text 외 근거까지 추적한다.
9. 모든 object는 common envelope을 가진다.
10. 모든 query/display/validation/index contract는 OntologyRegistry에서 관리한다.
```

한 줄로 정리하면 이겁니다.

> 원래 설계는 좋은 82점짜리다.
> 직전 보강안은 92점이다.
> 진짜 freeze하려면 `Entity + Observation + SupportLink + Registry + MetricObservation + BusinessFactor + BusinessEvent + AgreementTerm` 구조로 가야 한다.
> 그게 100점에 가까운 최종 스키마다.

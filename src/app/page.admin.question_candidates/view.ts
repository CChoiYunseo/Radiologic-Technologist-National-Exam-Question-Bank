import { OnInit } from '@angular/core';
import { Service } from '@wiz/libs/portal/season/service';

export class Component implements OnInit {
    constructor(public service: Service) { }

    public loading: boolean = false;
    public detailLoading: boolean = false;
    public saving: boolean = false;
    public authRequired: boolean = false;
    public errorMessage: string = "";

    public total: number = 0;
    public statuses: any[] = [];
    public subjects: any[] = [];
    public stages: any[] = [];
    public validations: any[] = [];
    public questionTypes: any[] = [];

    public rows: any[] = [];
    public selected: any = null;
    public selectedEvidence: any[] = [];
    public selectedValidations: any[] = [];

    public filters: any = {
        status: "pending_expert_review",
        subject: "",
        source_stage: "",
        question_type: "",
        keyword: "",
        page: 1,
        dump: 20
    };
    public reviewNote: string = "";

    public async ngOnInit() {
        await this.service.init();
        await this.load();
    }

    public async load() {
        this.loading = true;
        await this.loadSummary();
        await this.loadRows();
        this.loading = false;
        await this.service.render();
    }

    public async loadSummary() {
        const { code, data } = await wiz.call("summary");
        if (code === 200) {
            this.authRequired = false;
            this.errorMessage = "";
            this.total = data.total || 0;
            this.statuses = data.statuses || [];
            this.subjects = data.subjects || [];
            this.stages = data.stages || [];
            this.validations = data.validations || [];
            this.questionTypes = data.question_types || [];
        } else {
            this.handleApiFailure(code, data);
        }
    }

    public async loadRows() {
        const { code, data } = await wiz.call("candidates", this.filters);
        if (code === 200) {
            this.authRequired = false;
            this.errorMessage = "";
            this.rows = data.rows || [];
            this.total = data.total || this.total;
            if (!this.selected && this.rows.length > 0) {
                await this.select(this.rows[0]);
            }
        } else {
            this.handleApiFailure(code, data);
        }
    }

    public handleApiFailure(code: number, data: any = {}) {
        this.rows = [];
        this.selected = null;
        this.selectedEvidence = [];
        this.selectedValidations = [];
        if (code === 401) {
            this.authRequired = true;
            this.errorMessage = "관리자 로그인이 필요합니다.";
            return;
        }
        this.authRequired = false;
        this.errorMessage = data && data.message ? data.message : "검수 후보를 불러오지 못했습니다.";
    }

    public loginUrl() {
        return "/access?next=/admin/question-candidates";
    }

    public async search() {
        this.filters.page = 1;
        this.selected = null;
        this.selectedEvidence = [];
        this.selectedValidations = [];
        await this.loadRows();
        await this.service.render();
    }

    public async clearFilters() {
        this.filters.status = "";
        this.filters.subject = "";
        this.filters.source_stage = "";
        this.filters.question_type = "";
        this.filters.keyword = "";
        this.filters.page = 1;
        this.selected = null;
        this.selectedEvidence = [];
        this.selectedValidations = [];
        await this.loadRows();
        await this.service.render();
    }

    public async select(row: any) {
        if (!row || this.detailLoading) return;
        this.detailLoading = true;
        const { code, data } = await wiz.call("detail", { id: row.id });
        this.detailLoading = false;
        if (code === 200) {
            this.selected = data.candidate || row;
            this.selectedEvidence = data.evidence || [];
            this.selectedValidations = data.validations || [];
            this.reviewNote = "";
            await this.service.render();
        } else {
            await this.alert(data.message || "후보 상세 조회에 실패했습니다.");
        }
    }

    public async updateStatus(status: string) {
        if (!this.selected || this.saving) return;
        this.saving = true;
        const { code, data } = await wiz.call("update_status", {
            id: this.selected.id,
            status: status,
            note: this.reviewNote || ""
        });
        this.saving = false;
        if (code === 200) {
            this.selected.status = data.status;
            await this.alert("검수 상태를 저장했습니다.", "success");
            await this.loadSummary();
            await this.loadRows();
            await this.select(this.selected);
        } else {
            await this.alert(data.message || "검수 상태 저장에 실패했습니다.");
        }
    }

    public statusLabel(status: string) {
        const labels: any = {
            pending_expert_review: "검수 대기",
            needs_revision: "수정 필요",
            expert_rejected: "전문가 반려",
            expert_passed: "전문가 통과",
            expert_approved: "전문가 승인"
        };
        return labels[status] || status || "-";
    }

    public statusCount(status: string) {
        const row = (this.statuses || []).find((item: any) => item.status === status);
        return row ? row.count || 0 : 0;
    }

    public statusClass(status: string) {
        const classes: any = {
            pending_expert_review: "bg-amber-50 text-amber-700 border-amber-200",
            needs_revision: "bg-sky-50 text-sky-700 border-sky-200",
            expert_rejected: "bg-red-50 text-red-700 border-red-200",
            expert_passed: "bg-emerald-50 text-emerald-700 border-emerald-200",
            expert_approved: "bg-emerald-50 text-emerald-700 border-emerald-200"
        };
        return classes[status] || "bg-gray-50 text-gray-700 border-gray-200";
    }

    public stageLabel(stage: string) {
        const labels: any = {
            initial_llm_pass: "초안 2차 검증 통과",
            revised_llm_pass: "수정안 2차 검증 통과",
            recovered_llm_pass: "회수 문항 2차 검증 통과",
            subject_quota_llm_pass: "과목별 추가 생성 2차 검증 통과",
            visual_draft: "시각자료 초안"
        };
        return labels[stage] || stage || "-";
    }

    public stageCount(stage: string) {
        const row = (this.stages || []).find((item: any) => item.source_stage === stage);
        return row ? row.count || 0 : 0;
    }

    public questionTypeCount(type: string) {
        const row = (this.questionTypes || []).find((item: any) => item.question_type === type);
        return row ? row.count || 0 : 0;
    }

    public generationPolicy(item: any = null) {
        const target = item || this.selected;
        const payload = target && target.candidate_payload ? target.candidate_payload : {};
        return payload.policy || {};
    }

    public generationType(item: any = null) {
        const target = item || this.selected;
        const policy = this.generationPolicy(target);
        if (policy.generation_type) return policy.generation_type;
        return target && target.source_stage === "visual_draft" ? "visual_draft" : "text_draft";
    }

    public generationTypeLabel(item: any = null) {
        const labels: any = {
            visual_draft: "시각자료 기반",
            recovered_llm_pass: "회수 문항",
            text_draft: "텍스트 기반"
        };
        const target = item || this.selected;
        if (target && target.source_stage === "recovered_llm_pass") return labels.recovered_llm_pass;
        return labels[this.generationType(item)] || this.generationType(item);
    }

    public harnessStatus(item: any = null) {
        const target = item || this.selected;
        const policy = this.generationPolicy(target);
        if (policy.harness_status) return policy.harness_status;
        const summary = target && target.validation_summary ? target.validation_summary : {};
        if (summary.harness && summary.harness.overall_pass) return "passed";
        if (summary.visual_harness && summary.visual_harness.overall_pass) return "passed";
        return "-";
    }

    public visualSummary() {
        if (!this.selected || !this.selected.candidate_payload) return null;
        return this.selected.candidate_payload.visual_evidence_summary || null;
    }

    public visualAsset() {
        if (!this.selected) return null;
        return this.selected.visual_asset || null;
    }

    public hasVisualAsset() {
        const asset = this.visualAsset();
        return !!(asset && asset.available && asset.svg_markup);
    }

    public visualAssetPolicyText() {
        const asset = this.visualAsset();
        const policy = asset && asset.policy ? asset.policy : {};
        if (!asset) return "";
        if (policy.new_educational_diagram) {
            return "구조화 설명 기반 새 교육용 도식, 원본 이미지 미포함";
        }
        return "전문가 검수 전 미승인 시각자료";
    }

    public isVisual(item: any = null) {
        return this.generationType(item || this.selected) === "visual_draft";
    }

    public answerText(item: any = null) {
        const target = item || this.selected;
        const answer = this.answerValue(target);
        if (!answer) return "-";
        return `${answer}번`;
    }

    public draftItem(item: any = null) {
        const target = item || this.selected;
        const payload = target && target.candidate_payload ? target.candidate_payload : {};
        return payload.draft_item || {};
    }

    public stemText(item: any = null) {
        const target = item || this.selected;
        const draft = this.draftItem(target);
        return (target && target.stem) || draft.stem || "";
    }

    public optionsList(item: any = null) {
        const target = item || this.selected;
        const draft = this.draftItem(target);
        if (target && Array.isArray(target.options) && target.options.length > 0) return target.options;
        if (Array.isArray(draft.options)) return draft.options;
        return [];
    }

    public answerValue(item: any = null) {
        const target = item || this.selected;
        const draft = this.draftItem(target);
        return (target && target.answer) || draft.answer || "";
    }

    public explanationText(item: any = null) {
        const target = item || this.selected;
        const draft = this.draftItem(target);
        return (target && target.explanation) || draft.explanation || "";
    }

    public distractorStrategyText(item: any = null) {
        const target = item || this.selected;
        const draft = this.draftItem(target);
        return (target && target.distractor_strategy) || draft.distractor_strategy || "";
    }

    public optionText(option: any, index: number) {
        if (typeof option === "string") return `${index + 1}. ${option}`;
        if (option && option.text) return `${option.label || index + 1}. ${option.text}`;
        return `${index + 1}. ${option || ""}`;
    }

    public scopeText(item: any) {
        if (!item) return "";
        return [item.period, item.subject, item.field, item.area, item.detail].filter((x: any) => !!x).join(" / ");
    }

    public validationPassed(row: any) {
        return Number(row && row.passed) === 1;
    }

    public async alert(message: string, status: string = "error") {
        return await this.service.modal.show({
            title: "",
            message: message,
            cancel: false,
            actionBtn: status,
            action: "확인",
            status: status
        });
    }
}

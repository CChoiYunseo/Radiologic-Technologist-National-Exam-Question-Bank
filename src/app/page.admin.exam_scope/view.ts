import { OnInit } from '@angular/core';
import { Service } from '@wiz/libs/portal/season/service';

export class Component implements OnInit {
    constructor(public service: Service) { }

    public loading: boolean = false;
    public seeding: boolean = false;
    public previewing: boolean = false;
    public generating: boolean = false;

    public summary: any = {};
    public dbCount: number = 0;
    public requestCount: number = 0;

    public subjects: any[] = [];
    public fields: any[] = [];
    public areas: any[] = [];
    public details: any[] = [];
    public requests: any[] = [];
    public ragPreview: any = null;
    public ragEvidence: any[] = [];
    public selectedResult: any = null;
    public detailKeyword: string = "";

    public form: any = {
        period: "",
        subject: "",
        field: "",
        area: "",
        detail: "",
        question_count: 1,
        difficulty: "중",
        question_type: "개념형",
        focus: "",
        top_k: 6
    };

    public async ngOnInit() {
        await this.service.init();
        await this.load();
    }

    public async load() {
        this.loading = true;
        await this.loadSummary();
        await this.loadSubjects();
        await this.loadRequests();
        this.loading = false;
        await this.service.render();
    }

    public async loadSummary() {
        const { code, data } = await wiz.call("summary");
        if (code === 200) {
            this.summary = data.summary || {};
            this.dbCount = data.db_count || 0;
            this.requestCount = data.request_count || 0;
        }
    }

    public async seed() {
        if (this.seeding) return;
        this.seeding = true;
        const { code, data } = await wiz.call("seed");
        this.seeding = false;
        if (code === 200) {
            await this.alert(`기준표 ${data.result.total}개를 DB에 저장했습니다.`, "success");
            await this.load();
        } else {
            await this.alert(data.message || "기준표 저장에 실패했습니다.");
        }
    }

    public async loadSubjects() {
        const { code, data } = await wiz.call("subjects");
        if (code === 200) this.subjects = data.rows || [];
    }

    public async selectSubject() {
        const selected = this.subjects.find((x: any) => x.subject === this.form.subject);
        this.form.period = selected && selected.period ? selected.period : "";
        this.form.field = "";
        this.form.area = "";
        this.form.detail = "";
        this.fields = [];
        this.areas = [];
        this.details = [];
        this.clearPreview();
        const { code, data } = await wiz.call("fields", { subject: this.form.subject });
        if (code === 200) this.fields = data.rows || [];
    }

    public async selectSubjectRow(row: any) {
        this.form.subject = row.subject || "";
        this.form.period = row.period || "";
        await this.selectSubject();
    }

    public async selectField() {
        this.form.area = "";
        this.form.detail = "";
        this.areas = [];
        this.details = [];
        this.clearPreview();
        const { code, data } = await wiz.call("areas", {
            subject: this.form.subject,
            field: this.form.field
        });
        if (code === 200) this.areas = data.rows || [];
    }

    public async selectFieldRow(row: any) {
        this.form.field = row.field || "";
        await this.selectField();
    }

    public async selectArea() {
        this.form.detail = "";
        this.details = [];
        this.clearPreview();
        const { code, data } = await wiz.call("details", {
            subject: this.form.subject,
            field: this.form.field,
            area: this.form.area
        });
        if (code === 200) this.details = data.rows || [];
    }

    public async selectAreaRow(row: any) {
        this.form.area = row.area || "";
        await this.selectArea();
    }

    public applyDetail(row: any) {
        this.form.period = row.period || this.form.period;
        this.form.subject = row.subject || this.form.subject;
        this.form.field = row.field || this.form.field;
        this.form.area = row.area || this.form.area;
        this.form.detail = row.detail || "";
        if (row.question_count > 0) this.form.question_count = row.question_count;
        this.clearPreview();
    }

    public isSelected(key: string, value: string) {
        return (this.form[key] || "") === (value || "");
    }

    public filteredDetails() {
        const keyword = (this.detailKeyword || "").trim();
        if (!keyword) return this.details;
        return this.details.filter((item: any) => {
            return String(item.detail || "").indexOf(keyword) >= 0;
        });
    }

    public selectedScopeText() {
        const parts = [this.form.period, this.form.subject, this.form.field, this.form.area, this.form.detail];
        const values = parts.filter((item: any) => !!item);
        if (values.length === 0) return "출제범위를 선택해주세요.";
        return values.join(" / ");
    }

    public clearPreview() {
        this.ragPreview = null;
        this.ragEvidence = [];
        this.selectedResult = null;
    }

    public async previewRag() {
        if (!this.form.subject || !this.form.field || !this.form.area || !this.form.detail) {
            await this.alert("교시/과목/분야/영역/세부영역을 선택해주세요.");
            return;
        }
        this.previewing = true;
        const { code, data } = await wiz.call("preview_rag", this.form);
        this.previewing = false;
        if (code === 200) {
            this.ragPreview = data.payload || {};
            this.ragEvidence = this.ragPreview.source_evidence || [];
            await this.service.render();
        } else {
            this.clearPreview();
            await this.alert(data.message || "근거 검색에 실패했습니다.");
        }
    }

    public async createRequest() {
        if (!this.form.subject || !this.form.field || !this.form.area || !this.form.detail) {
            await this.alert("교시/과목/분야/영역/세부영역을 선택해주세요.");
            return;
        }
        const { code, data } = await wiz.call("create_request", this.form);
        if (code === 200) {
            this.ragPreview = data.row && data.row.request_payload ? data.row.request_payload : null;
            this.ragEvidence = this.ragPreview && this.ragPreview.source_evidence ? this.ragPreview.source_evidence : [];
            await this.alert("RAG 근거가 연결된 문항 생성 요청을 저장했습니다.", "success");
            await this.loadRequests();
        } else {
            await this.alert(data.message || "요청 저장에 실패했습니다.");
        }
    }

    public async loadRequests() {
        const { code, data } = await wiz.call("requests");
        if (code === 200) this.requests = data.rows || [];
    }

    public async runGeneration(item: any) {
        if (this.generating) return;
        this.generating = true;
        const { code, data } = await wiz.call("run_generation", { id: item.id });
        this.generating = false;
        if (code === 200) {
            this.selectedResult = data.row.request_payload || {};
            await this.alert("문항 생성 및 자동 검증을 실행했습니다.", "success");
            await this.loadRequests();
            await this.service.render();
        } else {
            await this.alert(data.message || "문항 생성 실행에 실패했습니다.");
        }
    }

    public showResult(item: any) {
        this.selectedResult = item.request_payload || {};
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

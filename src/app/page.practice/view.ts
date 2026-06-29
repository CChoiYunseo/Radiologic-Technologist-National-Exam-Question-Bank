import { OnInit } from '@angular/core';
import { Service } from '@wiz/libs/portal/season/service';

type PracticeQuestion = {
    id: string;
    number: number;
    period: string;
    subject: string;
    field: string;
    area: string;
    detail: string;
    question_type: string;
    difficulty: string;
    stem: string;
    options: { index: number; text: string }[];
};

export class Component implements OnInit {
    constructor(public service: Service) { }

    public loading: boolean = false;
    public grading: boolean = false;
    public summary: any = { total: 0, subjects: [], exams: [] };
    public period: string = "1교시";
    public sessionId: string = "";
    public examMeta: any = {};
    public questions: PracticeQuestion[] = [];
    public currentIndex: number = 0;
    public selectedChoices: any = {};
    public answers: any = {};
    public submitted: boolean = false;

    public async ngOnInit() {
        await this.service.init();
        await this.loadSummary();
        await this.startExam("1교시");
    }

    public async loadSummary() {
        const { code, data } = await wiz.call("summary");
        if (code === 200) this.summary = data || { total: 0, subjects: [], exams: [] };
    }

    public async startExam(period: string = this.period) {
        this.loading = true;
        this.period = period || "1교시";
        this.sessionId = "";
        this.examMeta = {};
        this.currentIndex = 0;
        this.selectedChoices = {};
        this.answers = {};
        this.submitted = false;
        const { code, data } = await wiz.call("start", { period: this.period });
        this.loading = false;
        if (code === 200) {
            this.sessionId = data.session_id || "";
            this.examMeta = data.exam || {};
            this.questions = data.questions || [];
            if (this.questions.length === 0) await this.alert("현재 풀 수 있는 문항이 없습니다.");
            await this.service.render();
        } else {
            await this.alert(data.message || "시험지를 불러오지 못했습니다.");
        }
    }

    public currentQuestion() {
        return this.questions[this.currentIndex] || null;
    }

    public currentResult() {
        const question = this.currentQuestion();
        if (!question) return null;
        return this.answers[question.id] || null;
    }

    public currentChoice() {
        const question = this.currentQuestion();
        if (!question) return 0;
        return Number(this.selectedChoices[question.id] || 0);
    }

    public async selectChoice(index: number) {
        if (this.submitted) return;
        const question = this.currentQuestion();
        if (!question) return;
        this.selectedChoices[question.id] = index;
        await this.service.render();
    }

    public async selectQuestionChoice(question: PracticeQuestion, index: number) {
        if (this.submitted || !question) return;
        this.selectedChoices[question.id] = index;
        await this.service.render();
    }

    public async next() {
        if (this.currentIndex < this.questions.length - 1) {
            this.currentIndex += 1;
            await this.service.render();
        }
    }

    public async prev() {
        if (this.currentIndex <= 0) return;
        this.currentIndex -= 1;
        await this.service.render();
    }

    public async go(index: number) {
        if (index < 0 || index >= this.questions.length) return;
        this.currentIndex = index;
        await this.service.render();
    }

    public selectedCount() {
        return Object.keys(this.selectedChoices || {}).filter((key: string) => !!this.selectedChoices[key]).length;
    }

    public unansweredCount() {
        return Math.max((this.questions || []).length - this.selectedCount(), 0);
    }

    public score() {
        return Object.keys(this.answers || {}).filter((key: string) => this.answers[key] && this.answers[key].correct).length;
    }

    public progress() {
        if (!this.questions.length) return 0;
        return Math.round((this.selectedCount() / this.questions.length) * 100);
    }

    public async submitExam() {
        if (this.grading || this.submitted || this.questions.length === 0) return;
        if (this.unansweredCount() > 0) {
            await this.alert(`아직 ${this.unansweredCount()}문항의 답안을 선택하지 않았습니다.`);
            return;
        }

        this.grading = true;
        const results: any = {};
        for (const question of this.questions) {
            const selected = Number(this.selectedChoices[question.id] || 0);
            const { code, data } = await wiz.call("answer", {
                id: question.id,
                session_id: this.sessionId,
                selected: selected
            });
            if (code === 200) results[question.id] = data;
        }
        this.answers = results;
        this.submitted = true;
        this.grading = false;
        await this.service.render();
    }

    public scopeText(question: PracticeQuestion) {
        if (!question) return "";
        return [question.period, question.subject, question.field, question.area].filter((x: string) => !!x).join(" / ");
    }

    public examWarning() {
        const target = Number(this.examMeta.target_questions || 0);
        const selected = Number(this.examMeta.selected_questions || this.questions.length || 0);
        if (target && selected < target) return `${this.period} 기준 ${target}문항 중 현재 ${selected}문항만 준비되었습니다. 부족분은 신규 텍스트 문항 생성 대상으로 분리했습니다.`;
        if (this.examMeta.complete === false) return "출제기준 대비 부족 영역이 있어 임시 시험지로 표시됩니다.";
        return "";
    }

    public optionClass(option: any) {
        const question = this.currentQuestion();
        if (!question) return "";
        const result = this.answers[question.id] || null;
        const selected = Number(this.selectedChoices[question.id] || 0);

        if (!this.submitted) {
            return selected === option.index
                ? "border-[#2563eb] bg-blue-50 text-slate-950 ring-2 ring-[#2563eb]"
                : "border-slate-300 bg-white text-slate-950 hover:border-slate-700";
        }
        if (result && option.index === result.correct_answer) return "border-emerald-600 bg-emerald-50 text-emerald-950";
        if (result && option.index === result.selected && !result.correct) return "border-rose-600 bg-rose-50 text-rose-950";
        return "border-slate-200 bg-white text-slate-500";
    }

    public optionMarkClass(option: any) {
        const question = this.currentQuestion();
        if (!question) return "";
        const result = this.answers[question.id] || null;
        const selected = Number(this.selectedChoices[question.id] || 0);

        if (!this.submitted) {
            return selected === option.index
                ? "border-[#2563eb] bg-[#2563eb] text-white"
                : "border-slate-400 bg-white text-slate-800";
        }
        if (result && option.index === result.correct_answer) return "border-emerald-600 bg-emerald-600 text-white";
        if (result && option.index === result.selected && !result.correct) return "border-rose-600 bg-rose-600 text-white";
        return "border-slate-300 bg-white text-slate-500";
    }

    public sheetBubbleClass(question: PracticeQuestion, index: number) {
        const selected = Number(this.selectedChoices[question.id] || 0);
        const result = this.answers[question.id] || null;
        if (this.submitted && result && index === result.correct_answer) return "border-emerald-600 bg-emerald-600 text-white";
        if (this.submitted && result && index === result.selected && !result.correct) return "border-rose-600 bg-rose-600 text-white";
        if (selected === index) return "border-[#2563eb] bg-[#2563eb] text-white";
        return "border-slate-300 bg-white text-slate-500";
    }

    public markerClass(index: number) {
        const question = this.questions[index];
        if (!question) return "";
        const selected = !!this.selectedChoices[question.id];
        const result = this.answers[question.id] || null;
        if (index === this.currentIndex) return "border-slate-950 bg-slate-950 text-white";
        if (this.submitted && result && result.correct) return "border-emerald-200 bg-emerald-100 text-emerald-800";
        if (this.submitted && result && !result.correct) return "border-rose-200 bg-rose-100 text-rose-800";
        if (selected) return "border-blue-200 bg-blue-50 text-blue-800";
        return "border-slate-200 bg-white text-slate-500";
    }

    public resultMessage() {
        const total = this.questions.length || 0;
        return `${total}문항 중 ${this.score()}문항 정답`;
    }

    public async alert(message: string) {
        return await this.service.modal.show({
            title: "",
            message: message,
            cancel: false,
            action: "확인",
            status: "error"
        });
    }
}

import { OnInit } from '@angular/core';
import { Service } from '@wiz/libs/portal/season/service';

type CourseCard = {
    title: string;
    period: string;
    subject: string;
    category: string;
    kind: string;
    description: string;
    count: string;
    readiness: string;
    progress: number;
    progressClass: string;
    accentClass: string;
};

type StatCard = {
    label: string;
    value: string;
    detail: string;
    badge: string;
    badgeClass: string;
};

type WorkflowItem = {
    title: string;
    description: string;
    dotClass: string;
};

type EvidenceRow = {
    name: string;
    status: string;
    progress: number;
    barClass: string;
};

type ReviewItem = {
    title: string;
    description: string;
    status: string;
    badgeClass: string;
};

export class Component implements OnInit {
    constructor(public service: Service) { }

    public query: string = '';
    public activeTab: string = '전체';
    public activeCategory: string = '전체';

    public tabs: string[] = ['전체', '1교시', '2교시', '3교시'];
    public categories: string[] = ['전체', '방사선이론', '방사선응용', '의료법규', '근거자료'];

    public statCards: StatCard[] = [
        {
            label: '출제범위',
            value: '250',
            detail: '검수 기준표 기준',
            badge: '완료',
            badgeClass: 'bg-emerald-50 text-emerald-700'
        },
        {
            label: '학습목표',
            value: '연결',
            detail: '범위 선택 기준 반영',
            badge: '운영',
            badgeClass: 'bg-blue-50 text-blue-700'
        },
        {
            label: '전공 자료',
            value: '추출중',
            detail: '텍스트 우선, 시각 자료 분리',
            badge: '진행',
            badgeClass: 'bg-amber-50 text-amber-700'
        },
        {
            label: '검증',
            value: '5단계',
            detail: '범위·근거·정답·저작권·표현',
            badge: '필수',
            badgeClass: 'bg-rose-50 text-rose-700'
        }
    ];

    public workflowItems: WorkflowItem[] = [
        {
            title: '범위 선택',
            description: '교시, 과목, 분야, 영역, 세부영역 기준으로 생성 요청을 구성합니다.',
            dotClass: 'bg-emerald-500'
        },
        {
            title: '근거 검색',
            description: '전공 자료에서 출처와 페이지가 있는 근거 chunk를 검색합니다.',
            dotClass: 'bg-blue-500'
        },
        {
            title: '문항 생성',
            description: 'Qwen3-VL 연결 전까지는 요청 구조와 검증 흐름을 먼저 고정합니다.',
            dotClass: 'bg-amber-500'
        },
        {
            title: '자동 검증',
            description: '통과한 문항도 최종 승인 전 검토 대기 상태로 유지합니다.',
            dotClass: 'bg-slate-400'
        }
    ];

    public courses: CourseCard[] = [
        {
            title: '의료법규',
            period: '1교시',
            subject: '의료관계법규',
            category: '의료법규',
            kind: '법규형',
            description: '의료법, 의료기사 등에 관한 법률, 지역보건법 기준 문항',
            count: '20문항',
            readiness: '범위 기준 확정',
            progress: 82,
            progressClass: 'bg-emerald-500',
            accentClass: 'bg-emerald-50 text-emerald-700'
        },
        {
            title: '방사선물리',
            period: '1교시',
            subject: '방사선이론',
            category: '방사선이론',
            kind: '개념·계산형',
            description: '방사선 기본 특성, 원자핵, 상호작용, 방사성원소',
            count: '9문항',
            readiness: '자료 수집 필요',
            progress: 44,
            progressClass: 'bg-amber-500',
            accentClass: 'bg-blue-50 text-blue-700'
        },
        {
            title: '전기전자개론',
            period: '1교시',
            subject: '방사선이론',
            category: '방사선이론',
            kind: '계산형',
            description: '직류회로, 교류회로, 자기장, 전기장, 반도체소자',
            count: '9문항',
            readiness: '텍스트 추출 완료',
            progress: 70,
            progressClass: 'bg-blue-500',
            accentClass: 'bg-blue-50 text-blue-700'
        },
        {
            title: '방사선장치(기기)',
            period: '1교시',
            subject: '방사선이론',
            category: '방사선이론',
            kind: '장비·도식형',
            description: '엑스선 발생, 엑스선관, 고전압장치, 제어장치',
            count: '9문항',
            readiness: '시각자료 검수 대기',
            progress: 76,
            progressClass: 'bg-blue-500',
            accentClass: 'bg-blue-50 text-blue-700'
        },
        {
            title: '방사선관리',
            period: '1교시',
            subject: '방사선이론',
            category: '방사선이론',
            kind: '안전관리',
            description: '방사선안전관리, 모니터링, 관계법규, 장해방어',
            count: '9문항',
            readiness: '법규 매핑 검토',
            progress: 58,
            progressClass: 'bg-amber-500',
            accentClass: 'bg-blue-50 text-blue-700'
        },
        {
            title: '방사선영상',
            period: '2교시',
            subject: '방사선응용',
            category: '방사선응용',
            kind: '절차형',
            description: '상지, 하지, 골반, 척추, 흉부, 복부 촬영 문항',
            count: '20문항군',
            readiness: '자료 추출 대기',
            progress: 36,
            progressClass: 'bg-amber-500',
            accentClass: 'bg-violet-50 text-violet-700'
        },
        {
            title: 'CT',
            period: '2교시',
            subject: '방사선응용',
            category: '방사선응용',
            kind: '응용형',
            description: 'CT 기초이론, 장치, 영상 재구성, 화질 및 선량',
            count: '9문항',
            readiness: '자료 연결 준비',
            progress: 54,
            progressClass: 'bg-amber-500',
            accentClass: 'bg-violet-50 text-violet-700'
        },
        {
            title: 'MRI',
            period: '2교시',
            subject: '방사선응용',
            category: '방사선응용',
            kind: '개념형',
            description: '기본원리, 강조영상, 펄스시퀀스, 인공물, 안전',
            count: '9문항',
            readiness: '자료 수집 필요',
            progress: 20,
            progressClass: 'bg-rose-500',
            accentClass: 'bg-violet-50 text-violet-700'
        },
        {
            title: '핵의학 검사',
            period: '2교시',
            subject: '방사선응용',
            category: '방사선응용',
            kind: '장비형',
            description: '방사성의약품, 핵의학 기기, 체내검사, PET/SPECT',
            count: '15문항',
            readiness: '근거 자료 일부 확보',
            progress: 48,
            progressClass: 'bg-amber-500',
            accentClass: 'bg-violet-50 text-violet-700'
        },
        {
            title: '방사선치료',
            period: '2교시',
            subject: '방사선응용',
            category: '방사선응용',
            kind: '응용형',
            description: '선량분포, 치료계획, 선형가속기, 특수치료기술',
            count: '15문항',
            readiness: '근거 자료 일부 확보',
            progress: 46,
            progressClass: 'bg-amber-500',
            accentClass: 'bg-violet-50 text-violet-700'
        },
        {
            title: '실기시험 영상 품질관리',
            period: '3교시',
            subject: '실기시험',
            category: '근거자료',
            kind: '시각자료',
            description: '영상·장비 기반 문항은 별도 검수와 시각 자료 해석이 필요',
            count: '50문항군',
            readiness: '후속 단계',
            progress: 18,
            progressClass: 'bg-rose-500',
            accentClass: 'bg-slate-100 text-slate-700'
        }
    ];

    public evidenceRows: EvidenceRow[] = [
        { name: '텍스트 레이어 추출', status: '진행', progress: 68, barClass: 'bg-blue-500' },
        { name: '스캔본 OCR 검토', status: '검수', progress: 38, barClass: 'bg-amber-500' },
        { name: '표 구조화', status: '검수', progress: 28, barClass: 'bg-amber-500' },
        { name: '수식·도식 crop', status: '검수', progress: 32, barClass: 'bg-amber-500' },
        { name: 'Qwen3-VL 해석', status: '대기', progress: 8, barClass: 'bg-rose-500' }
    ];

    public reviewQueue: ReviewItem[] = [
        {
            title: '방사선장치 176-180쪽',
            description: '표, 수식, 파형 그림의 의미 해석 초안이 생성되었습니다.',
            status: '검토',
            badgeClass: 'bg-amber-50 text-amber-700'
        },
        {
            title: '전공 자료 전체 추출',
            description: '본문 텍스트 우선 추출 후 표·수식·그림은 별도 대기열로 분리합니다.',
            status: '예정',
            badgeClass: 'bg-slate-100 text-slate-600'
        },
        {
            title: '문항 생성 승인 정책',
            description: '자동 생성 문항은 검증 통과 후에도 최종 승인 전에는 저장하지 않습니다.',
            status: '적용',
            badgeClass: 'bg-emerald-50 text-emerald-700'
        }
    ];

    public filteredCourses(): CourseCard[] {
        let keyword = (this.query || '').trim().toLowerCase();
        return this.courses.filter(course => {
            let tabMatched = this.activeTab === '전체' || course.period === this.activeTab;
            let categoryMatched = this.activeCategory === '전체' || course.category === this.activeCategory;
            let text = `${course.title} ${course.period} ${course.subject} ${course.category} ${course.kind} ${course.description} ${course.readiness}`.toLowerCase();
            let keywordMatched = !keyword || text.includes(keyword);
            return tabMatched && categoryMatched && keywordMatched;
        });
    }

    public categoryCount(category: string): number {
        if (category === '전체') return this.courses.length;
        return this.courses.filter(course => course.category === category).length;
    }

    public selectTab(tab: string) {
        this.activeTab = tab;
    }

    public selectCategory(category: string) {
        this.activeCategory = category;
    }

    public start(course?: CourseCard) {
        let keyword = course ? course.title : this.query;
        let suffix = keyword ? `?keyword=${encodeURIComponent(keyword)}` : '';
        location.href = `/admin/exam-scope${suffix}`;
    }

    public async ngOnInit() {
        await this.service.init();
    }
}

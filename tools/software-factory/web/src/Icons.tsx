// Copyright (c) Microsoft Corporation. All rights reserved.

import type { SVGProps } from "react";

export interface IconProps extends SVGProps<SVGSVGElement> {
    size?: number;
}

function SvgBase({ size = 15, className, children, ...rest }: IconProps) {
    return (
        <svg
            width={size}
            height={size}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.9}
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            focusable="false"
            className={className ? `sf-icon ${className}` : "sf-icon"}
            {...rest}
        >
            {children}
        </svg>
    );
}

/** Stacked layers — Feature Studio surface */
export function LayersIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <polygon points="12 2.5 2.5 7.5 12 12.5 21.5 7.5 12 2.5" />
            <polyline points="2.5 12.5 12 17.5 21.5 12.5" />
            <polyline points="2.5 17.5 12 22.5 21.5 17.5" />
        </SvgBase>
    );
}

/** Bars + upward trend — Scorecard & Analytics surface */
export function ChartTrendIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <path d="M3 20h18" />
            <path d="M6 16v-4" />
            <path d="M11 16V9" />
            <path d="M16 16v-6" />
            <polyline points="5 9 10.5 5 14.5 7.5 20 3" />
            <polyline points="16.5 3 20 3 20 6.5" />
        </SvgBase>
    );
}

/** Connected workflow nodes — Pipeline Stages tab */
export function WorkflowIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <rect x="3" y="3" width="6" height="6" rx="1.5" />
            <rect x="15" y="3" width="6" height="6" rx="1.5" />
            <rect x="9" y="15" width="6" height="6" rx="1.5" />
            <path d="M9 6h6" />
            <path d="M6 9v3a3 3 0 0 0 3 3" />
            <path d="M18 9v3a3 3 0 0 1-3 3" />
        </SvgBase>
    );
}

/** Branch diff — Revision Diffs tab */
export function GitCompareIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <circle cx="6" cy="6" r="2.5" />
            <circle cx="18" cy="18" r="2.5" />
            <path d="M13 6h3a2 2 0 0 1 2 2v7.5" />
            <polyline points="15.5 3.5 13 6 15.5 8.5" />
            <path d="M11 18H8a2 2 0 0 1-2-2V8.5" />
            <polyline points="8.5 20.5 11 18 8.5 15.5" />
        </SvgBase>
    );
}

/** Terminal prompt — Subagent Transcripts / Flight Recorder tab */
export function TerminalIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <rect x="2.5" y="4" width="19" height="16" rx="2.5" />
            <polyline points="7 9.5 10.5 12.5 7 15.5" />
            <line x1="12.5" y1="15.5" x2="17" y2="15.5" />
        </SvgBase>
    );
}

/** AI Sparkles — Ask Copilot & contextual Ask AI chips */
export function SparklesIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <path d="M11 2.5 12.9 8.1 18.5 10 12.9 11.9 11 17.5 9.1 11.9 3.5 10 9.1 8.1Z" />
            <path d="M19 15.5v4" />
            <path d="M17 17.5h4" />
            <path d="M18.5 3v3" />
            <path d="M17 4.5h3" />
        </SvgBase>
    );
}

/** Configuration sliders — Connections drawer */
export function SlidersIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <line x1="4" y1="6" x2="20" y2="6" />
            <line x1="4" y1="12" x2="20" y2="12" />
            <line x1="4" y1="18" x2="20" y2="18" />
            <circle cx="9" cy="6" r="2" fill="var(--bg-surface, currentColor)" />
            <circle cx="15" cy="12" r="2" fill="var(--bg-surface, currentColor)" />
            <circle cx="10" cy="18" r="2" fill="var(--bg-surface, currentColor)" />
        </SvgBase>
    );
}

/** Diagnostic pulse — Check setup (Doctor) */
export function ActivityPulseIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <polyline points="2.5 12 6.5 12 9.5 5 14.5 19 17.5 12 21.5 12" />
        </SvgBase>
    );
}

/** Refresh arrows — Sync state */
export function RefreshIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <path d="M20.5 11a8.5 8.5 0 0 0-14.8-5.7L3.5 7.5" />
            <polyline points="3.5 3.5 3.5 7.5 7.5 7.5" />
            <path d="M3.5 13a8.5 8.5 0 0 0 14.8 5.7l2.2-2.2" />
            <polyline points="20.5 20.5 20.5 16.5 16.5 16.5" />
        </SvgBase>
    );
}

/** Sun — Switch to Light theme */
export function SunIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <circle cx="12" cy="12" r="4" />
            <line x1="12" y1="2.5" x2="12" y2="5" />
            <line x1="12" y1="19" x2="12" y2="21.5" />
            <line x1="4.9" y1="4.9" x2="6.7" y2="6.7" />
            <line x1="17.3" y1="17.3" x2="19.1" y2="19.1" />
            <line x1="2.5" y1="12" x2="5" y2="12" />
            <line x1="19" y1="12" x2="21.5" y2="12" />
            <line x1="4.9" y1="19.1" x2="6.7" y2="17.3" />
            <line x1="17.3" y1="6.7" x2="19.1" y2="4.9" />
        </SvgBase>
    );
}

/** Moon — Switch to Dark theme */
export function MoonIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <path d="M20.5 14.5A8.5 8.5 0 1 1 9.5 3.5a6.8 6.8 0 0 0 11 11Z" />
        </SvgBase>
    );
}

/** Plus — New feature */
export function PlusIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
        </SvgBase>
    );
}

/** Left arrow — Back navigation */
export function ArrowLeftIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <line x1="19" y1="12" x2="5" y2="12" />
            <polyline points="11 18 5 12 11 6" />
        </SvgBase>
    );
}

/** Checkmark */
export function CheckIcon(props: IconProps) {
    return (
        <SvgBase strokeWidth={2.4} {...props}>
            <polyline points="4.5 12.5 9.5 17.5 19.5 6.5" />
        </SvgBase>
    );
}

/** Lock — Read-only sidecar & shared lock */
export function LockIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <rect x="4.5" y="11" width="15" height="10" rx="2" />
            <path d="M8 11V7.5a4 4 0 0 1 8 0V11" />
        </SvgBase>
    );
}

/** Bolt / AI Credits metric */
export function ZapIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <polygon points="13 2.5 4 14 11.5 14 10.5 21.5 20 10 12.5 10 13 2.5" />
        </SvgBase>
    );
}

/** CPU / Active Agent Compute metric */
export function CpuIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <rect x="5" y="5" width="14" height="14" rx="2" />
            <rect x="9" y="9" width="6" height="6" rx="1" />
            <line x1="9" y1="2" x2="9" y2="5" />
            <line x1="15" y1="2" x2="15" y2="5" />
            <line x1="9" y1="19" x2="9" y2="22" />
            <line x1="15" y1="19" x2="15" y2="22" />
            <line x1="2" y1="9" x2="5" y2="9" />
            <line x1="2" y1="15" x2="5" y2="15" />
            <line x1="19" y1="9" x2="22" y2="9" />
            <line x1="19" y1="15" x2="22" y2="15" />
        </SvgBase>
    );
}

/** Stage 1: Define success / Brief & Criteria */
export function FileCheckIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <path d="M14 2.5H6.5A2 2 0 0 0 4.5 4.5v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8Z" />
            <polyline points="14 2.5 14 8 19.5 8" />
            <polyline points="9 14.5 11 16.5 15.5 12" />
        </SvgBase>
    );
}

/** Stage 2: Architecture Design */
export function CompassIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <circle cx="12" cy="12" r="9.2" />
            <polygon points="15.8 8.2 13.5 13.5 8.2 15.8 10.5 10.5 15.8 8.2" />
        </SvgBase>
    );
}

/** Stage 3: PBI Plan & Dependency Waves */
export function ListTreeIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <path d="M8 6h12" />
            <path d="M11 12h9" />
            <path d="M11 18h9" />
            <circle cx="4.5" cy="6" r="1.5" />
            <path d="M4.5 7.5V16a2 2 0 0 0 2 2H8" />
            <path d="M4.5 12H8" />
        </SvgBase>
    );
}

/** Stage 4: Create ADO Work Items / Backlog */
export function KanbanIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <rect x="3" y="3.5" width="18" height="17" rx="2" />
            <line x1="9" y1="3.5" x2="9" y2="20.5" />
            <line x1="15" y1="3.5" x2="15" y2="20.5" />
            <line x1="5.5" y1="8" x2="6.5" y2="8" />
            <line x1="11.5" y1="8" x2="12.5" y2="8" />
            <line x1="11.5" y1="12" x2="12.5" y2="12" />
            <line x1="17.5" y1="8" x2="18.5" y2="8" />
        </SvgBase>
    );
}

/** Stage 5: Implementation & Pull Request */
export function GitPullRequestIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <circle cx="6" cy="6" r="2.5" />
            <circle cx="6" cy="18" r="2.5" />
            <circle cx="18" cy="18" r="2.5" />
            <line x1="6" y1="8.5" x2="6" y2="15.5" />
            <path d="M13 6h3a2 2 0 0 1 2 2v7.5" />
            <polyline points="15.5 3.5 13 6 15.5 8.5" />
        </SvgBase>
    );
}

/** Stage 6: Verify & Finish (CI + Spec Audit) */
export function ShieldCheckIcon(props: IconProps) {
    return (
        <SvgBase {...props}>
            <path d="M12 2.5 4.5 5.8v5.7c0 5 3.2 9.3 7.5 10.8 4.3-1.5 7.5-5.8 7.5-10.8V5.8Z" />
            <polyline points="8.8 12.2 11 14.5 15.5 9.8" />
        </SvgBase>
    );
}

/** Renders the semantic icon for pipeline stage 0..5 */
export function StageIcon({ index, ...props }: IconProps & { index: number }) {
    switch (index) {
        case 0:
            return <FileCheckIcon {...props} />;
        case 1:
            return <CompassIcon {...props} />;
        case 2:
            return <ListTreeIcon {...props} />;
        case 3:
            return <KanbanIcon {...props} />;
        case 4:
            return <GitPullRequestIcon {...props} />;
        default:
            return <ShieldCheckIcon {...props} />;
    }
}

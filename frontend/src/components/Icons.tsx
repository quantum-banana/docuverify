import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement>

const base = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
}

export const ShieldIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <path d="M12 3 19 6v5.4c0 4.5-2.9 7.7-7 9.6-4.1-1.9-7-5.1-7-9.6V6l7-3Z" />
    <path d="m8.8 12 2.1 2.1 4.5-4.6" />
  </svg>
)

export const UploadIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <path d="M12 16V4" />
    <path d="m7.5 8.5 4.5-4.5 4.5 4.5" />
    <path d="M5 14.5v3.2A2.3 2.3 0 0 0 7.3 20h9.4a2.3 2.3 0 0 0 2.3-2.3v-3.2" />
  </svg>
)

export const FileIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <path d="M7 3h7l4 4v14H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z" />
    <path d="M14 3v5h4" />
    <path d="M9 13h6M9 17h4" />
  </svg>
)

export const SparkIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <path d="m12 3 1.3 4.1L17 9l-3.7 1.9L12 15l-1.3-4.1L7 9l3.7-1.9L12 3Z" />
    <path d="m18.5 14 .7 2.3 2.3.7-2.3.7-.7 2.3-.7-2.3-2.3-.7 2.3-.7.7-2.3Z" />
  </svg>
)

export const LockIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <rect x="5" y="10" width="14" height="10" rx="2" />
    <path d="M8 10V7a4 4 0 0 1 8 0v3" />
  </svg>
)

export const CheckIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <path d="m5 12.5 4.2 4.2L19 7" />
  </svg>
)

export const ChevronIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <path d="m9 18 6-6-6-6" />
  </svg>
)

export const CloseIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <path d="M6 6 18 18M18 6 6 18" />
  </svg>
)

export const AlertIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <path d="M12 3 2.8 19h18.4L12 3Z" />
    <path d="M12 9v4M12 16.8v.2" />
  </svg>
)

export const EyeIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <path d="M2.5 12s3.4-6 9.5-6 9.5 6 9.5 6-3.4 6-9.5 6-9.5-6-9.5-6Z" />
    <circle cx="12" cy="12" r="2.5" />
  </svg>
)

export const RefreshIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <path d="M20 7v5h-5" />
    <path d="M18.3 16.5A8 8 0 1 1 20 12" />
  </svg>
)

export const ArrowIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <path d="M5 12h14M14 7l5 5-5 5" />
  </svg>
)

export const ScanIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3M7 12h10" />
  </svg>
)

export const SignalIcon = (props: IconProps) => (
  <svg {...base} {...props}>
    <path d="M5 16.5a10 10 0 0 1 14 0M8 13a5.7 5.7 0 0 1 8 0M11 9.5a1.5 1.5 0 0 1 2 0" />
    <circle cx="12" cy="19" r=".8" fill="currentColor" stroke="none" />
  </svg>
)

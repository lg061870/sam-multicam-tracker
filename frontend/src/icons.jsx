const line = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
};

const Svg = ({ size = 16, children, ...props }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden={props['aria-label'] ? undefined : true} {...props}>
    {children}
  </svg>
);

export const Play = (props) => (
  <Svg {...props} fill="currentColor"><polygon points="6 4 20 12 6 20 6 4" /></Svg>
);

export const Pause = (props) => (
  <Svg {...props} fill="currentColor">
    <rect x="6" y="4" width="4" height="16" rx="1" />
    <rect x="14" y="4" width="4" height="16" rx="1" />
  </Svg>
);

export const RotateCcw = (props) => (
  <Svg {...props} {...line}>
    <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
    <path d="M3 3v5h5" />
  </Svg>
);

export const ChevronLeft = (props) => (
  <Svg {...props} {...line}><polyline points="15 18 9 12 15 6" /></Svg>
);

export const ChevronRight = (props) => (
  <Svg {...props} {...line}><polyline points="9 18 15 12 9 6" /></Svg>
);

export const Check = (props) => (
  <Svg {...props} {...line}><path d="M20 6 9 17l-5-5" /></Svg>
);

export const Plus = (props) => (
  <Svg {...props} {...line}><path d="M12 5v14" /><path d="M5 12h14" /></Svg>
);

export const X = (props) => (
  <Svg {...props} {...line}><path d="M18 6 6 18" /><path d="m6 6 12 12" /></Svg>
);

export const Spinner = () => <span className="spinner" aria-hidden="true" />;

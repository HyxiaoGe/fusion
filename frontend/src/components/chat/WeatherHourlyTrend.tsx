'use client';

import { useTranslation } from 'react-i18next';

import type { WeatherHourlyPoint } from '@/types/conversation';

import styles from './StructuredToolResults.module.css';

const WIDTH = 720;
const HEIGHT = 150;
const PAD_X = 18;
const TEMP_TOP = 22;
const TEMP_BOTTOM = 78;
const BAR_TOP = 92;
const BAR_BOTTOM = 126;
const LABEL_Y = 144;
const LABEL_EVERY = 3;

/** 当地时间的小时数：直接取 ISO 字符串中的小时，避免被浏览器时区换算。 */
function localHour(time: string): number {
  return Number(time.slice(11, 13));
}

function formatTemp(value: number): string {
  return `${Math.round(value)}°`;
}

export function WeatherHourlyTrend({ hourly }: { hourly: WeatherHourlyPoint[] }) {
  const { t } = useTranslation();
  if (hourly.length < 2) return null;

  const temps = hourly.map(point => point.temp_c);
  const minTemp = Math.min(...temps);
  const maxTemp = Math.max(...temps);
  const span = Math.max(maxTemp - minTemp, 1);
  const step = (WIDTH - PAD_X * 2) / (hourly.length - 1);
  const x = (index: number) => PAD_X + index * step;
  const y = (temp: number) => TEMP_BOTTOM - ((temp - minTemp) / span) * (TEMP_BOTTOM - TEMP_TOP);
  const linePath = hourly
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${x(index).toFixed(1)},${y(point.temp_c).toFixed(1)}`)
    .join(' ');
  const barWidth = Math.min(step * 0.6, 16);
  const hasPop = hourly.some(point => typeof point.pop === 'number');
  const maxPop = Math.max(0, ...hourly.map(point => point.pop ?? 0));
  const summary = t('structuredResults.weather.hourly.summary', {
    min: formatTemp(minTemp),
    max: formatTemp(maxTemp),
    pop: maxPop,
  });

  return (
    <div className={styles.weatherHourly} data-testid="weather-hourly-trend">
      <div className={styles.weatherHourlyHeader}>
        <span className={styles.weatherHourlyTitle}>{t('structuredResults.weather.hourly.title')}</span>
        <span className={styles.weatherHourlyLegend}>
          <span className={styles.legendTemp} aria-hidden="true" />
          {t('structuredResults.weather.hourly.temperature')}
          {hasPop && (
            <>
              <span className={styles.legendPop} aria-hidden="true" />
              {t('structuredResults.weather.hourly.precipitationChance')}
            </>
          )}
        </span>
      </div>
      <div className={styles.weatherHourlyScroll}>
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className={styles.weatherHourlyChart}
          role="img"
          aria-label={summary}
        >
          {hasPop && hourly.map((point, index) => {
            const pop = point.pop ?? 0;
            const barHeight = (pop / 100) * (BAR_BOTTOM - BAR_TOP);
            return (
              <g key={`pop-${point.time}`}>
                <rect
                  data-testid="weather-hourly-pop"
                  x={x(index) - barWidth / 2}
                  y={BAR_BOTTOM - barHeight}
                  width={barWidth}
                  height={Math.max(barHeight, pop > 0 ? 1 : 0)}
                  rx={2}
                  className={styles.hourlyPopBar}
                />
                {pop >= 30 && (
                  <text x={x(index)} y={BAR_BOTTOM - barHeight - 3} className={styles.hourlyPopLabel}>
                    {pop}%
                  </text>
                )}
              </g>
            );
          })}
          <line x1={PAD_X} x2={WIDTH - PAD_X} y1={BAR_BOTTOM} y2={BAR_BOTTOM} className={styles.hourlyBaseline} />
          <path d={linePath} className={styles.hourlyTempLine} />
          {hourly.map((point, index) => {
            const showLabel = index % LABEL_EVERY === 0 || index === hourly.length - 1;
            return (
              <g key={`temp-${point.time}`}>
                <circle cx={x(index)} cy={y(point.temp_c)} r={showLabel ? 2.6 : 1.6} className={styles.hourlyTempDot} />
                {showLabel && (
                  <>
                    <text x={x(index)} y={y(point.temp_c) - 7} className={styles.hourlyTempLabel}>
                      {formatTemp(point.temp_c)}
                    </text>
                    <text x={x(index)} y={LABEL_Y} className={styles.hourlyTimeLabel}>
                      {t('structuredResults.weather.hourly.hour', { hour: localHour(point.time) })}
                    </text>
                  </>
                )}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

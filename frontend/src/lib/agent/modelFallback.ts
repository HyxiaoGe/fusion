/** 换备用模型的原因（后端 model_unavailable_reason / circuit_open）转成用户能读懂的说法。 */
export function modelFallbackReasonLabel(reason: string | null | undefined): string {
  if (!reason) return '原因未知';
  if (reason === 'first_output_timeout') return '首字超时';
  if (reason === 'stream_idle_timeout') return '输出中途卡住';
  if (reason === 'circuit_open') return '近期连续失败，已熔断';
  if (reason === 'network_error') return '网络错误';
  const status = /^http_(\d{3})$/.exec(reason);
  return status ? `服务商返回 ${status[1]}` : reason;
}

import hljs from 'highlight.js/lib/common';
import dockerfile from 'highlight.js/lib/languages/dockerfile';
import nginx from 'highlight.js/lib/languages/nginx';
import powershell from 'highlight.js/lib/languages/powershell';

// 只打包常用语言（common 自带 36 种），完整版有 190 多种、压缩后约 360KB；
// 未注册的语言由调用方回退为自动识别或纯文本。
hljs.registerLanguage('dockerfile', dockerfile);
hljs.registerLanguage('nginx', nginx);
hljs.registerLanguage('powershell', powershell);

export default hljs;

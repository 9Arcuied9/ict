// 高德开放平台 -> 应用管理 -> 添加 Key，服务平台选 “Web端(JS API)”。
// 两个值都要填；不要把真实密钥提交到公开仓库。
window.MAP_CONFIG = {
  key: '',
  securityJsCode: '',
  defectsUrl: 'defects.json', // 后端就绪后改成真实接口，例如 /api/defects
  trackUrl: 'track.json',     // 可选：画出行程轨迹，用来核对坐标是否落在路面上
};

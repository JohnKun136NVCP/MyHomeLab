/* Ajustes del portal «Cine en casa» (se puede editar sin reconstruir nada). */
window.CINE = {
  title: "Cine en casa",
  // Jellyfin detrás del mismo nginx (así no hay problemas de CORS ni de certificados)
  jellyfin: "/jellyfin",
  // Si el navegador no reproduce HLS por sí solo, se carga hls.js de este CDN (solo esa vez)
  hlsjs: "https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js",
  // Calidad máxima cuando hay que convertir el video (bits por segundo)
  maxBitrate: 20000000,
};

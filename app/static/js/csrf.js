// 目的：同源 session 寫入帶 CSRF；不把 token 傳給外站，不改 body／credentials／signal。
// 驗證：Node Request／Headers 控制及三流程真瀏覽器。後端仍逐次驗 token。
(() => {
    'use strict';
    const fetch=window.fetch;
    window.fetch=function(input,init){
        const method=String(init?.method || input?.method || 'GET').toUpperCase();
        const url=new URL(typeof input==='string' || input instanceof URL ? input : input.url,location.href);
        if(url.origin===location.origin && ['POST','PUT','PATCH','DELETE'].includes(method)){
            const headers=new Headers(init?.headers || input?.headers || {});
            const token=document.querySelector('meta[name="csrf-token"]')?.content;
            if(token && !headers.has('X-CSRFToken'))headers.set('X-CSRFToken',token);
            return fetch.call(this,input,{...init,headers});
        }
        return fetch.call(this,input,init);
    };
})();

/**
 * @fileoverview 任务通知的系统级推送：把 WS 的 notifications 事件弹成 Windows 原生通知。
 */

import {useEffect} from 'react'
import {api} from '../api/client'

type TaskNotification = {instance?: string; title?: string; content?: string}

function show(data: TaskNotification) {
  if (typeof Notification === 'undefined' || Notification.permission === 'denied') return
  if (Notification.permission === 'default') {
    // 首条通知到达时才向浏览器要权限，用户允许后立即补弹这一条。
    void Notification.requestPermission().then(permission => { if (permission === 'granted') show(data) })
    return
  }
  const notification = new Notification(data.title ?? 'AzurPilot', {
    body: data.content ?? '',
    icon: `${import.meta.env.BASE_URL}azurpilot.svg`,
  })
  notification.onclick = () => { window.focus(); notification.close() }
}

export function useSystemNotifications() {
  useEffect(() => api.onEvent(event => {
    if (event.topic !== 'notifications') return
    show(event.data as TaskNotification)
  }), [])
}

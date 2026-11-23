include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-xbox-accel
PKG_VERSION:=0.3.0
PKG_RELEASE:=1
PKG_LICENSE:=MIT
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/luci-app-xbox-accel
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=Game download CDN optimization and Xbox HTTP redirect
  DEPENDS:=+luci-base +rpcd +curl +ca-bundle +uhttpd +jshn
endef

define Package/luci-app-xbox-accel/description
  Download-only DNS routing, scheduled CDN tests, bounded logs and optional
  HTTP redirect from Xbox international download URLs to domestic URLs.
  Requires the existing dnsmasq or dnsmasq-full service; does not replace it.
endef

define Package/luci-app-xbox-accel/conffiles
/etc/config/xbox-accel
endef

define Build/Compile
endef

define Package/luci-app-xbox-accel/install
	$(INSTALL_DIR) $(1)/usr/bin $(1)/etc/init.d $(1)/etc/config
	$(INSTALL_BIN) ./files/usr/bin/xbox-accel $(1)/usr/bin/
	$(INSTALL_BIN) ./files/etc/init.d/xbox-accel $(1)/etc/init.d/
	$(INSTALL_CONF) ./files/etc/config/xbox-accel $(1)/etc/config/
	$(INSTALL_DIR) $(1)/usr/share/xbox-accel
	$(CP) ./files/usr/share/xbox-accel/* $(1)/usr/share/xbox-accel/
	$(INSTALL_BIN) ./files/usr/share/xbox-accel/redirect.cgi ./files/usr/share/xbox-accel/firewall.sh $(1)/usr/share/xbox-accel/
	$(INSTALL_DIR) $(1)/usr/libexec/rpcd $(1)/usr/share/rpcd/acl.d $(1)/usr/share/luci/menu.d
	$(INSTALL_BIN) ./files/usr/libexec/rpcd/xbox-accel $(1)/usr/libexec/rpcd/
	$(INSTALL_DATA) ./files/usr/share/rpcd/acl.d/*.json $(1)/usr/share/rpcd/acl.d/
	$(INSTALL_DATA) ./files/usr/share/luci/menu.d/*.json $(1)/usr/share/luci/menu.d/
	$(INSTALL_DIR) $(1)/www/luci-static/resources/view/xbox-accel
	$(INSTALL_DATA) ./files/www/luci-static/resources/view/xbox-accel/*.js $(1)/www/luci-static/resources/view/xbox-accel/
endef

define Package/luci-app-xbox-accel/postinst
#!/bin/sh
[ -n "$$IPKG_INSTROOT" ] && exit 0
/etc/init.d/xbox-accel enable
/etc/init.d/rpcd restart
[ "$$(uci -q get xbox-accel.main.enabled)" = 1 ] && /etc/init.d/xbox-accel restart
exit 0
endef

define Package/luci-app-xbox-accel/prerm
#!/bin/sh
[ -n "$$IPKG_INSTROOT" ] && exit 0
/etc/init.d/xbox-accel stop || exit 1
/etc/init.d/xbox-accel disable
exit 0
endef

$(eval $(call BuildPackage,luci-app-xbox-accel))

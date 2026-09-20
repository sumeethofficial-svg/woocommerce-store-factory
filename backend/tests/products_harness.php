<?php
class WP_CLI {
    public static $log = array();
    public static function log($m) { self::$log[] = 'log: ' . $m; }
    public static function warning($m) { self::$log[] = 'warning: ' . $m; }
}
$GLOBALS['products'] = array();
class WC_Product_Simple {
    private $d = array();
    public function set_name($v) { $this->d['name'] = $v; }
    public function set_status($v) { $this->d['status'] = $v; }
    public function set_catalog_visibility($v) { $this->d['visibility'] = $v; }
    public function set_regular_price($v) { $this->d['price'] = $v; }
    public function set_description($v) { $this->d['description'] = $v; }
    public function set_short_description($v) { $this->d['short'] = $v; }
    public function update_meta_data($k, $v) { $this->d['meta'][$k] = $v; }
    public function save() {
        $id = count($GLOBALS['products']) + 1;
        $this->d['id'] = $id;
        $GLOBALS['products'][$id] = $this->d;
        return $id;
    }
}
function get_posts($args) {
    $found = array();
    foreach ($GLOBALS['products'] as $id => $p) {
        if (($p['meta'][$args['meta_key']] ?? null) === $args['meta_value']) {
            $found[] = $id;
        }
    }
    return $found;
}
$script = $argv[1];
include $script;
include $script;
echo json_encode(array('products' => array_values($GLOBALS['products']), 'log' => WP_CLI::$log));

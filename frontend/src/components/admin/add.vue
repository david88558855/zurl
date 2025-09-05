<template>
    <div class="add">
        <el-form 
            label-position="top"
            :model="formData"
            ref="formRef"
            :rules="rules"
        >
            <el-form-item label="长链接" prop="long_url">
                <el-input @blur="getLinkInfo(formData.long_url)" v-model="formData.long_url"></el-input>
            </el-form-item>

            <el-form-item label="自定义短链接">
                <el-input
                v-model="formData.short_url"
                placeholder="short_url"
                >
                <template #prepend>/</template>
                </el-input>
            </el-form-item>

            <el-form-item label="标题">
                <el-input v-model="formData.title"></el-input>
            </el-form-item>

            <el-form-item label="有效期(天)" v-if="props.utype === 'add'">
                <el-input v-model="formData.ttl_days" type="number" placeholder="0代表永不过期" value="0"></el-input>
            </el-form-item>

            <el-form-item>
                <el-button v-if="props.utype === 'add'" type="primary" @click="addLink">添加链接</el-button>
                <el-button v-else-if="props.utype === 'edit'" type="primary" @click="updateLink">更新链接</el-button>
                <el-button @click="resetForm">重置</el-button>
            </el-form-item>

        </el-form>
    </div>
</template>

<script setup>
import { ref,onMounted } from 'vue'
import req, { toForm } from '@/utils/req'
import { ElMessage } from 'element-plus'

const props = defineProps(['url', 'utype'])

// 添加 emit 定义，完成事件
const emit = defineEmits(['finish'])

const formRef = ref(null)
const formData = ref({
    long_url: '',
    short_url: '',
    title: '',
    ttl_days: 0
})
const rules = {
    long_url: [
        { required: true, message: '长链接不能为空', trigger: 'blur' },
        { type: 'url', message: '请输入有效的URL', trigger: 'blur' }
    ]}

// 添加链接函数
const addLink = () => {
    formRef.value.validate((valid) => {
        if (valid) {
            req.post("/api/shorten_url", formData.value)
            .then(res => {
                if (res.data.code === 200) {
                    ElMessage.success("链接添加成功")
                    // 触发 finish 事件，关闭弹窗
                    emit('finish')
                    // 重置表单
                    resetForm()
                } else {        
                    ElMessage.error(res.data.msg || "添加链接失败")
                }
            })
            .catch(err => {
                console.error(err)
                ElMessage.error("添加链接时发生错误")
            })
        } else {
            ElMessage.error("请填写正确的链接信息")
            formRef.value.clearValidate()
            formData.value = {
                long_url: '',
                short_url: '',
                title: ''
            }
        }
    })
}

// 更新链接函数
const updateLink = () => {
    formRef.value.validate((valid) => {
        if (valid) {
            let dataToUpdate = {
                description: "",
                long_url: formData.value.long_url,
                short_url: formData.value.short_url,
                title: formData.value.title
            }
            req.post("/api/update_url/" + formData.value.id, dataToUpdate)
            .then(res => {
                if (res.data.code === 200) {
                    ElMessage.success("链接更新成功")
                    emit('finish') // 触发 finish 事件，关闭弹窗
                    // 重置表单
                    resetForm()
                } else {
                    ElMessage.error(res.data.msg || "更新链接失败")
                }
            })
        } else {
            ElMessage.error("请填写正确的链接信息")
            formRef.value.clearValidate()
            formData.value = {
                long_url: '',
                short_url: '',
                title: ''
            }
        }
    })
}

// 重置表单
const resetForm = () => {
    formRef.value.resetFields()
    formData.value = {
        long_url: '',
        short_url: '',
        title: ''
    }
}

onMounted(() => {
    console.log(props.utype)
    if (props.url) {
        formData.value = props.url
    }
    else {
        resetForm()
    }
})

// 获取链接信息
const getLinkInfo = (url)=>{
    if (!url) {
        // ElMessage.warning("请输入长链接")
        return
    }
    req.post("/api/get_url_metadata", toForm({url:formData.value.long_url}))
    .then(res=>{
        if(res.data.code == 200){
            formData.value.title = res.data.data.title
            // formData.value.short_url = res.data.data.short_url
        }
        else{
            // ElMessage.error(res.data.msg)
        }
    })
    .catch(err=>{
        console.log(err)
        // ElMessage.error("获取链接信息失败")
    })
}
</script>

<style scoped>

</style>